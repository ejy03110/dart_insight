"""No model downloads or provider calls: mock encoder, real temporary Chroma."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from dart_insight.config import ConfigurationError, Settings, load_settings
from dart_insight.vectorstore.embeddings import (
    DIMENSION, LOCAL_MODEL, PROFILE, LocalEmbeddingError, LocalEmbeddings, create_embeddings,
)
from dart_insight.vectorstore.metadata import (
    MetadataDocument, MetadataIndexError, check_collection_model, index_metadata,
    open_metadata_collection, query_metadata,
)
from dart_insight.search_metadata import main as search_main


class MockLocalEmbeddings:
    model_name = LOCAL_MODEL

    def __init__(self):
        self.document_calls = []
        self.query_calls = []

    def embed_documents(self, texts):
        self.document_calls.append(texts)
        return [[1.0] + [0.0] * (DIMENSION - 1) for _ in texts]

    def embed_query(self, text):
        self.query_calls.append(text)
        return [1.0] + [0.0] * (DIMENSION - 1)


class LocalAdapterTests(unittest.TestCase):
    def test_constructor_and_factory_are_lazy_then_query_and_documents_share_encoder(self):
        model = Mock()
        model.encode.side_effect = lambda texts, **_: np.ones((len(texts), DIMENSION), dtype=np.float32)
        constructor = Mock(return_value=model)
        with patch.dict("sys.modules", {"sentence_transformers": SimpleNamespace(SentenceTransformer=constructor)}):
            adapter = create_embeddings(Settings(), batch_size=3)
            constructor.assert_not_called()
            self.assertEqual(len(adapter.embed_documents(["문서"])[0]), DIMENSION)
            self.assertEqual(len(adapter.embed_query("질문")), DIMENSION)
        constructor.assert_called_once()
        self.assertEqual(constructor.call_args.args, (LOCAL_MODEL,))
        self.assertFalse(constructor.call_args.kwargs["trust_remote_code"])
        self.assertEqual(constructor.call_args.kwargs["device"], "cpu")
        for call in model.encode.call_args_list:
            self.assertTrue(call.kwargs["normalize_embeddings"])
            self.assertEqual(call.kwargs["batch_size"], 3)
        self.assertEqual(model.encode.call_args_list[1].args[0], ["질문"])

    def test_empty_input_does_not_load_model(self):
        adapter = LocalEmbeddings()
        with patch.object(adapter, "_load") as load:
            self.assertEqual(adapter.embed_documents([]), [])
            load.assert_not_called()

    def test_unsupported_model_rejected_and_legacy_openai_model_variable_ignored(self):
        with self.assertRaises(ConfigurationError):
            LocalEmbeddings(model_name="text-embedding-3-small")
        with patch.dict("os.environ", {"OPENAI_EMBEDDING_MODEL": "text-embedding-3-small"}, clear=True), \
             patch("dart_insight.config.dotenv_values", return_value={}):
            self.assertEqual(load_settings().embedding_model, LOCAL_MODEL)

    def test_model_load_error_and_wrong_dimension_are_safe(self):
        constructor = Mock(side_effect=RuntimeError("private-token"))
        with patch.dict("sys.modules", {"sentence_transformers": SimpleNamespace(SentenceTransformer=constructor)}):
            with self.assertRaises(LocalEmbeddingError) as caught:
                LocalEmbeddings().embed_query("질문")
            self.assertNotIn("private-token", str(caught.exception))
        adapter = LocalEmbeddings()
        adapter._model = Mock()
        adapter._model.encode.return_value = np.ones((1, 8))
        with self.assertRaises(LocalEmbeddingError):
            adapter.embed_documents(["문서"])


class LocalVectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.collection = open_metadata_collection(self.root / "bge", LOCAL_MODEL)
        self.embeddings = MockLocalEmbeddings()
        self.docs = [MetadataDocument("meta:20250101000001", "공시 내용", {
            "corp_code": "10000001", "corp_name": "기업 A", "rcept_no": "20250101000001"}),
                     MetadataDocument("meta:20250101000002", "다른 공시", {
            "corp_code": "10000002", "corp_name": "기업 B", "rcept_no": "20250101000002"})]

    def test_local_upsert_repeat_and_filtered_query_use_1024_dim_model(self):
        self.assertEqual(self.collection.metadata["embedding_profile"], PROFILE)
        first = index_metadata(self.docs, self.collection, self.embeddings, model=LOCAL_MODEL)
        self.assertEqual(first["after"]["total"], 2)
        second = index_metadata(self.docs, self.collection, self.embeddings, model=LOCAL_MODEL)
        self.assertEqual(second["embedded_count"], 0)
        self.assertEqual(second["count_delta"], 0)
        self.assertEqual(len(self.embeddings.document_calls), 1)
        result = query_metadata(self.collection, "공시", self.embeddings, model=LOCAL_MODEL, corp_code="10000001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["metadata"]["corp_code"], "10000001")
        self.assertEqual(self.embeddings.query_calls, ["공시"])
        self.assertEqual(self.collection.count(), 2)

    def test_old_openai_vectors_preserved_and_new_database_can_index(self):
        legacy = open_metadata_collection(self.root / "old", "text-embedding-3-small")
        legacy.upsert(ids=["legacy"], documents=["old"], embeddings=[[1.0, 2.0, 3.0]])
        with self.assertRaises(MetadataIndexError):
            open_metadata_collection(self.root / "old", LOCAL_MODEL)
        self.assertEqual(legacy.count(), 1)
        self.assertEqual(legacy.metadata["embedding_model"], "text-embedding-3-small")
        result = index_metadata(self.docs, self.collection, self.embeddings, model=LOCAL_MODEL)
        self.assertEqual(result["after"]["total"], 2)

    def test_unverified_profile_and_wrong_stored_dimension_blocked(self):
        self.collection.upsert(ids=["wrong"], documents=["wrong"], embeddings=[[1.0, 2.0]])
        with self.assertRaises(MetadataIndexError):
            check_collection_model(self.collection, LOCAL_MODEL)
        self.assertEqual(self.collection.count(), 1)
        other = open_metadata_collection(self.root / "unverified", "old")
        other.modify(metadata={"embedding_model": LOCAL_MODEL})
        with self.assertRaises(MetadataIndexError):
            open_metadata_collection(self.root / "unverified", LOCAL_MODEL)

    def test_search_and_index_model_mismatch_fail_before_embedding(self):
        wrong = MockLocalEmbeddings()
        wrong.model_name = "other-model"
        with self.assertRaises(MetadataIndexError):
            index_metadata(self.docs, self.collection, wrong, model=LOCAL_MODEL)
        with self.assertRaises(MetadataIndexError):
            query_metadata(self.collection, "질문", wrong, model=LOCAL_MODEL)
        self.assertEqual(wrong.document_calls, [])
        self.assertEqual(wrong.query_calls, [])

    def test_empty_collection_or_missing_company_skips_query_encoding(self):
        self.assertEqual(query_metadata(self.collection, "질문", self.embeddings, model=LOCAL_MODEL), [])
        index_metadata(self.docs, self.collection, self.embeddings, model=LOCAL_MODEL)
        self.assertEqual(query_metadata(self.collection, "질문", self.embeddings,
                                        model=LOCAL_MODEL, corp_code="99999999"), [])
        self.assertEqual(self.embeddings.query_calls, [])

    def test_search_cli_uses_same_factory_and_never_creates_missing_database(self):
        index_metadata(self.docs, self.collection, self.embeddings, model=LOCAL_MODEL)
        with patch("dart_insight.search_metadata.load_settings", return_value=Settings(data_dir=self.root)), \
             patch("dart_insight.search_metadata.create_embeddings", return_value=self.embeddings) as factory, \
             redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()):
            self.assertEqual(search_main(["--query", "공시", "--db-dir", str(self.root / "bge")]), 0)
            self.assertIn(LOCAL_MODEL, stdout.getvalue())
            factory.assert_called_once()
            factory.reset_mock()
            self.assertEqual(search_main(["--query", "공시", "--db-dir", str(self.root / "missing")]), 1)
            factory.assert_not_called()
        self.assertFalse((self.root / "missing").exists())


if __name__ == "__main__":
    unittest.main()
