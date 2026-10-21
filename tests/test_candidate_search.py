"""F2004-2 offline searches, including real persistent Chroma with fake vectors."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dart_insight.data.documents import DocumentCandidate
from dart_insight.data.models import Disclosure
from dart_insight.services.candidate_search import CandidateSearchError, search_candidates
from dart_insight.vectorstore.embeddings import LOCAL_MODEL, PROFILE, DIMENSION
from dart_insight.vectorstore.metadata import MetadataDocument, open_metadata_collection, index_metadata


def disclosure(receipt="20250101000001", name="삼성전자"):
    return Disclosure("00126380", name, "005930", "사업보고서", receipt, "2025-01-01", "정기공시",
                      f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}", "2026-10-21T00:00:00Z")


def result_row(doc, distance):
    return {"id": f"meta:{doc.rcept_no}", "metadata": doc.to_dict(), "text": doc.to_index_text(),
            "distance": distance}


class CandidateSearchTests(unittest.TestCase):
    def setUp(self):
        self.selected = disclosure()
        self.other = disclosure("20250101000002")
        self.collection = Mock()
        self.collection.name = "dart_metadata"
        self.collection.metadata = {"embedding_model": LOCAL_MODEL, "embedding_profile": PROFILE,
                                    "hnsw:space": "cosine"}
        self.collection.get.return_value = {"ids": []}
        self.embeddings = Mock(model_name=LOCAL_MODEL)

    def search(self, queries, **kwargs):
        return search_candidates(self.collection, queries, self.selected, self.embeddings,
                                 model=LOCAL_MODEL, **kwargs)

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_multiple_queries_deduplicate_and_preserve_all_evidence(self, query):
        query.side_effect = [[result_row(self.other, .4)], [result_row(self.other, .1)]]
        result = self.search([" 위험 ", "조건", "위험"], top_k=2, corp_code="00126380")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.searched_queries, ("위험", "조건"))
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(len(result.candidates), 2)
        hit = result.candidates[1]
        self.assertIsInstance(hit.candidate, DocumentCandidate)
        self.assertEqual(hit.candidate.item_rcept_no, self.selected.rcept_no)
        self.assertEqual(hit.candidate.title, self.other.report_nm)
        self.assertEqual(hit.corp_name, self.other.corp_name)
        self.assertEqual(hit.candidate.source_url, self.other.source_url)
        self.assertEqual([(m.query, m.distance) for m in hit.matches], [("위험", .4), ("조건", .1)])
        self.assertAlmostEqual(hit.best_similarity, .9)
        self.assertEqual(hit.best_distance, .1)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args.kwargs["top_k"], 2)
        self.assertEqual(query.call_args.kwargs["corp_code"], "00126380")

    @patch("dart_insight.services.candidate_search.query_metadata", return_value=[])
    def test_empty_results_still_force_selected_without_invented_score(self, query):
        result = self.search(["위험"])
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.empty_queries, ("위험",))
        hit, = result.candidates
        self.assertTrue(hit.is_selected)
        self.assertEqual(hit.candidate.document_rcept_no, self.selected.rcept_no)
        self.assertEqual(hit.matches, ())
        self.assertIsNone(hit.best_distance)
        self.assertIsNone(hit.best_similarity)

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_selected_result_is_merged_even_with_worse_score(self, query):
        query.return_value = [result_row(self.other, .1), result_row(self.selected, 1.5)]
        result = self.search(["위험"])
        self.assertEqual(len(result.candidates), 2)
        self.assertTrue(result.candidates[0].is_selected)
        self.assertEqual(result.candidates[0].matches[0].distance, 1.5)

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_empty_query_list_includes_selected_and_makes_no_search(self, query):
        result = self.search([])
        self.assertEqual(result.status, "empty")
        self.assertEqual(len(result.candidates), 1)
        query.assert_not_called()

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_failure_is_not_partial_or_empty_success_and_is_sanitized(self, query):
        query.side_effect = [[result_row(self.other, .1)], RuntimeError("private-key")]
        with self.assertRaises(CandidateSearchError) as error:
            self.search(["위험", "조건"])
        self.assertNotIn("private-key", str(error.exception))

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_missing_or_conflicting_metadata_is_not_fabricated(self, query):
        for field in ("report_nm", "corp_name", "source_url", "rcept_no", "corp_code", "source_type"):
            row = result_row(self.other, .2)
            del row["metadata"][field]
            query.return_value = [row]
            with self.subTest(field=field), self.assertRaises(CandidateSearchError):
                self.search(["위험"])
        query.return_value = [result_row(replace(self.selected, corp_name="다른 기업"), .2)]
        with self.assertRaises(CandidateSearchError):
            self.search(["위험"])

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_invalid_distances_and_ids_fail(self, query):
        for distance in (float("nan"), float("inf"), None, "0.1", True):
            query.return_value = [result_row(self.other, distance)]
            with self.subTest(distance=distance), self.assertRaises(CandidateSearchError):
                self.search(["위험"])
        row = result_row(self.other, .1)
        row["id"] = "meta:wrong"
        query.return_value = [row]
        with self.assertRaises(CandidateSearchError):
            self.search(["위험"])

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_invalid_inputs_fail_before_search(self, query):
        for queries in ("위험", [" "], [None]):
            with self.subTest(queries=queries), self.assertRaises(CandidateSearchError):
                self.search(queries)
        for top_k in (0, 101, True, 1.5):
            with self.subTest(top_k=top_k), self.assertRaises(CandidateSearchError):
                self.search(["위험"], top_k=top_k)
        query.assert_not_called()

    @patch("dart_insight.services.candidate_search.query_metadata")
    def test_collection_and_embedding_model_contracts_checked_before_search(self, query):
        self.embeddings.model_name = "other-model"
        with self.assertRaises(CandidateSearchError):
            self.search([])
        self.embeddings.model_name = LOCAL_MODEL
        for field, value in (("embedding_model", "old-model"), ("embedding_profile", "bad"),
                             ("hnsw:space", "l2")):
            original = self.collection.metadata.copy()
            self.collection.metadata[field] = value
            with self.subTest(field=field), self.assertRaises(CandidateSearchError):
                self.search(["위험"])
            self.collection.metadata = original
        self.collection.name = "dart_documents"
        with self.assertRaises(CandidateSearchError):
            self.search(["위험"])
        query.assert_not_called()


class ChromaCandidateTests(unittest.TestCase):
    def test_real_query_metadata_with_mock_1024_dim_vectors_and_no_writes(self):
        class Encoder:
            model_name = LOCAL_MODEL

            def embed_documents(self, texts):
                return [[1.0] + [0.0] * (DIMENSION - 1) for _ in texts]

            def embed_query(self, text):
                return [1.0] + [0.0] * (DIMENSION - 1)

        with tempfile.TemporaryDirectory() as directory:
            collection = open_metadata_collection(Path(directory), LOCAL_MODEL)
            selected, other = disclosure(), disclosure("20250101000002")
            # Index test fixtures only. The search service never indexes documents.
            index_metadata([MetadataDocument(f"meta:{other.rcept_no}", other.to_index_text(), other.to_dict())],
                           collection, Encoder(), model=LOCAL_MODEL)
            before = collection.get(include=["metadatas", "documents"])
            result = search_candidates(collection, ["위험", "조건"], selected, Encoder(), model=LOCAL_MODEL, top_k=1)
            self.assertEqual(len(result.candidates), 2)
            self.assertEqual(len(result.candidates[1].matches), 2)
            self.assertEqual(collection.get(include=["metadatas", "documents"]), before)
            with self.assertRaises(CandidateSearchError):
                search_candidates(collection, ["위험"], selected, Encoder(), model="wrong-model")
