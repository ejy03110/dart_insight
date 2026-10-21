"""Offline F2002 checks: synthetic F2001 JSON, real local Chroma, mock embeddings."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dart_insight.config import Settings
from dart_insight.data.models import COMPANIES, Disclosure
from dart_insight.index_metadata import main
from dart_insight.vectorstore.metadata import (
    MetadataIndexError, collection_counts, index_metadata, load_metadata, open_metadata_collection,
)

MODEL = "offline-test-model"


def snapshot():
    rows = []
    companies = [{"name": company.name, "corp_name": company.name, "stock_code": company.stock_code,
                  "corp_code": f"{10000001 + index:08d}"} for index, company in enumerate(COMPANIES)]
    for index in range(100):
        company = companies[index % 3]
        doc = Disclosure.from_api({**company, "report_nm": f"테스트 공시 {index}",
                                   "rcept_no": f"20250101{index:06d}", "rcept_dt": "20250101"},
                                  "정기공시", "2026-01-01T00:00:00+00:00")
        rows.append({**doc.to_dict(), "index_text": doc.to_index_text()})
    return {"schema_version": 1, "requirement": "F2001", "summary": {
        "status": "complete", "total_unique_count": 100, "companies": companies,
        "company_counts": {"삼성전자": 34, "현대자동차": 33, "금양": 33}}, "disclosures": rows}


class MockEmbeddings:
    """Deterministic eight-dimensional local vectors; no provider or model download."""

    def __init__(self):
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[byte / 255 for byte in hashlib.sha256(text.encode()).digest()[:8]] for text in texts]


class MetadataInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "arbitrary-input.json"

    def load(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return load_metadata(self.path)

    def test_valid_f2001_ids_text_and_allowlisted_metadata(self):
        data = snapshot()
        data["disclosures"][0]["api_key"] = "must-not-be-stored"
        docs = self.load(data)
        self.assertEqual(len(docs), 100)
        self.assertEqual(docs[0].id, "meta:20250101000000")
        self.assertEqual(docs[0].text, data["disclosures"][0]["index_text"])
        self.assertNotIn("api_key", docs[0].metadata)
        for field in ("corp_code", "corp_name", "rcept_no", "report_nm", "rcept_dt", "source_url"):
            self.assertIn(field, docs[0].metadata)

    def test_malformed_json_and_incomplete_snapshot_rejected(self):
        self.path.write_text("not JSON")
        with self.assertRaises(MetadataIndexError):
            load_metadata(self.path)
        for value in ([], {}, {**snapshot(), "schema_version": 2}):
            with self.subTest(value=type(value).__name__), self.assertRaises(MetadataIndexError):
                self.load(value)
        data = snapshot()
        data["summary"]["status"] = "insufficient_data"
        with self.assertRaises(MetadataIndexError):
            self.load(data)

    def test_missing_fields_invalid_identifiers_dates_urls_and_text(self):
        mutations = [("corp_code", "123"), ("rcept_no", "bad"), ("report_nm", ""),
                     ("rcept_dt", "2025-02-30"), ("index_text", "unrelated text"),
                     ("source_url", "https://dart.fss.or.kr/?rcpNo=20250101000000&api_key=secret")]
        for key, value in mutations:
            data = snapshot()
            data["disclosures"][0][key] = value
            with self.subTest(key=key), self.assertRaises(MetadataIndexError):
                self.load(data)
        data = snapshot()
        del data["disclosures"][0]["corp_name"]
        with self.assertRaises(MetadataIndexError):
            self.load(data)

    def test_duplicate_ids_counts_and_company_mapping_rejected(self):
        for variant in ("duplicate", "count", "company_count", "company_code", "short"):
            data = snapshot()
            if variant == "duplicate":
                data["disclosures"][-1] = deepcopy(data["disclosures"][0])
            elif variant == "count":
                data["summary"]["total_unique_count"] = 101
            elif variant == "company_count":
                data["summary"]["company_counts"]["삼성전자"] = 35
            elif variant == "company_code":
                data["summary"]["companies"][0]["corp_code"] = "99999999"
            else:
                data["disclosures"] = data["disclosures"][:99]
                data["summary"]["total_unique_count"] = 99
            with self.subTest(variant=variant), self.assertRaises(MetadataIndexError):
                self.load(data)


class MetadataIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        path = self.directory / "input.json"
        path.write_text(json.dumps(snapshot(), ensure_ascii=False), encoding="utf-8")
        self.documents = load_metadata(path)
        self.collection = open_metadata_collection(self.directory / "db", MODEL)
        self.embeddings = MockEmbeddings()

    def run_index(self, documents=None, embeddings=None):
        return index_metadata(documents or self.documents, self.collection, embeddings or self.embeddings,
                              model=MODEL, batch_size=32)

    def test_persisted_upsert_second_run_skips_all_and_company_filter_works(self):
        first = self.run_index()
        self.assertEqual(first["after"], {"total": 100, "by_corp_code": {
            "10000001": 34, "10000002": 33, "10000003": 33}})
        self.assertEqual(first["count_delta"], 100)
        self.assertEqual(first["verified_input_count"], 100)
        self.assertEqual(sum(map(len, self.embeddings.calls)), 100)
        self.collection = open_metadata_collection(self.directory / "db", MODEL)
        second = self.run_index()
        self.assertEqual(second["count_delta"], 0)
        self.assertEqual(second["embedded_count"], 0)
        self.assertEqual(second["skipped_count"], 100)
        self.assertEqual(sum(map(len, self.embeddings.calls)), 100)
        filtered = self.collection.get(where={"corp_code": "10000002"}, include=["metadatas", "documents"])
        self.assertEqual(len(filtered["ids"]), 33)
        self.assertTrue(all(m["corp_code"] == "10000002" for m in filtered["metadatas"]))
        for identifier, text, metadata in zip(filtered["ids"], filtered["documents"], filtered["metadatas"]):
            self.assertEqual(identifier, "meta:" + metadata["rcept_no"])
            self.assertIn(metadata["report_nm"], text)

    def test_changed_text_updates_same_id_without_count_increase(self):
        self.run_index()
        changed = list(self.documents)
        changed[0] = replace(changed[0], text=changed[0].text + "\n정정 내용")
        result = self.run_index(changed)
        self.assertEqual(result["embedded_count"], 1)
        self.assertEqual(result["after"]["total"], 100)
        self.assertEqual(result["count_delta"], 0)
        self.assertEqual(self.collection.get(ids=[changed[0].id], include=["documents"])["documents"], [changed[0].text])

    def test_model_mismatch_aborts_without_embeddings(self):
        with self.assertRaises(MetadataIndexError):
            open_metadata_collection(self.directory / "db", "other-model")
        self.assertEqual(self.collection.metadata["embedding_model"], MODEL)
        with self.assertRaises(MetadataIndexError):
            index_metadata(self.documents, self.collection, self.embeddings, model="other-model")
        self.assertEqual(self.embeddings.calls, [])

    def test_failure_hides_credentials_and_rerun_resumes_partial_batches(self):
        provider = MockEmbeddings()
        original = provider.embed_documents

        def fail_second(texts):
            if provider.calls:
                raise RuntimeError("secret-key-in-provider-error")
            return original(texts)

        provider.embed_documents = fail_second
        with self.assertRaises(MetadataIndexError) as caught:
            self.run_index(embeddings=provider)
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertEqual(self.collection.count(), 32)
        result = self.run_index()
        self.assertEqual(result["embedded_count"], 68)
        self.assertEqual(result["skipped_count"], 32)
        self.assertEqual(result["after"]["total"], 100)

    def test_invalid_vectors_are_not_written(self):
        for output in ([], [[float("nan")]] * 32, [[1.0], [1.0, 2.0]] * 16):
            provider = MockEmbeddings()
            provider.embed_documents = lambda texts: output
            with self.subTest(output_length=len(output)), self.assertRaises(MetadataIndexError):
                self.run_index(embeddings=provider)
            self.assertEqual(self.collection.count(), 0)

    def test_subset_does_not_delete_existing_records(self):
        self.run_index()
        result = self.run_index(self.documents[:10])
        self.assertEqual(result["after"]["total"], 100)
        self.assertEqual(result["verified_input_count"], 10)
        self.assertEqual(collection_counts(self.collection, ["10000001"], batch_size=10)["by_corp_code"],
                         {"10000001": 34})


class MetadataCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "collection.json"
        self.path.write_text(json.dumps(snapshot(), ensure_ascii=False), encoding="utf-8")
        self.settings = Settings(data_dir=self.directory, embedding_model=MODEL)

    def test_dry_run_no_key_no_database_no_embedding_client(self):
        stdout = StringIO()
        with patch("dart_insight.index_metadata.load_settings", return_value=self.settings), \
             patch("dart_insight.index_metadata.create_embeddings") as factory, \
             patch("dart_insight.index_metadata.open_metadata_collection") as database, redirect_stdout(stdout):
            status = main(["--input", str(self.path), "--dry-run"])
        self.assertEqual(status, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["document_count"], 100)
        self.assertEqual(report["unique_id_count"], 100)
        self.assertEqual(report["embedding_model"], MODEL)
        factory.assert_not_called()
        database.assert_not_called()
        self.assertFalse((self.directory / "chroma").exists())

    def test_preflight_printed_before_mock_embeddings_and_repeat_cli_count(self):
        stdout, provider = StringIO(), MockEmbeddings()

        def factory(settings):
            self.assertIn('"document_count": 100', stdout.getvalue())
            self.assertIn(MODEL, stdout.getvalue())
            return provider

        with patch("dart_insight.index_metadata.load_settings", return_value=self.settings), \
             patch("dart_insight.index_metadata.create_embeddings", side_effect=factory), redirect_stdout(stdout):
            self.assertEqual(main(["--input", str(self.path)]), 0)
            self.assertEqual(main(["--input", str(self.path)]), 0)
        self.assertIn('"count_delta": 0', stdout.getvalue())
        self.assertIn('"skipped_count": 100', stdout.getvalue())
        self.assertEqual(sum(map(len, provider.calls)), 100)

    def test_invalid_input_never_creates_embedding_client(self):
        self.path.write_text("{}")
        with patch("dart_insight.index_metadata.load_settings", return_value=self.settings), \
             patch("dart_insight.index_metadata.create_embeddings") as factory, \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(["--input", str(self.path)]), 1)
            factory.assert_not_called()
        self.assertFalse((self.directory / "chroma").exists())


if __name__ == "__main__":
    unittest.main()
