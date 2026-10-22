"""Child-only Chroma integration checks. Invoked by test_candidate_search.py.

The parent owns the temporary path and deletes it only after this process exits.
No private Chroma shutdown API, global reset, or production DB path is used.
"""

from contextlib import contextmanager, ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Direct script invocation works with Windows paths and the current interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb

from dart_insight.data.models import Disclosure
from dart_insight.services.candidate_search import CandidateSearchError, search_candidates
from dart_insight.vectorstore.embeddings import DIMENSION, LOCAL_MODEL
from dart_insight.vectorstore.metadata import MetadataDocument, open_metadata_collection, index_metadata


@contextmanager
def managed_metadata_collection(directory):
    constructor = chromadb.PersistentClient
    with ExitStack() as clients:
        def create_client(*args, **kwargs):
            return clients.enter_context(constructor(*args, **kwargs))

        with patch("chromadb.PersistentClient", side_effect=create_client):
            yield open_metadata_collection(directory, LOCAL_MODEL)


class Encoder:
    model_name = LOCAL_MODEL

    def embed_documents(self, texts):
        return [[1.0] + [0.0] * (DIMENSION - 1) for _ in texts]

    def embed_query(self, text):
        return [1.0] + [0.0] * (DIMENSION - 1)


def disclosure(receipt):
    return Disclosure("00126380", "삼성전자", "005930", "사업보고서", receipt, "2025-01-01", "정기공시",
                      f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}", "2026-10-21T00:00:00Z")


def main(mode, directory):
    check = unittest.TestCase()
    with managed_metadata_collection(directory) as collection:
        if mode == "search":
            selected, other = disclosure("20250101000001"), disclosure("20250101000002")
            index_metadata([MetadataDocument(f"meta:{other.rcept_no}", other.to_index_text(), other.to_dict())],
                           collection, Encoder(), model=LOCAL_MODEL)
            before = collection.get(include=["metadatas", "documents"])
            result = search_candidates(collection, ["위험", "조건"], selected, Encoder(), model=LOCAL_MODEL, top_k=1)
            check.assertEqual(len(result.candidates), 2)
            check.assertEqual(result.status, "ok")
            check.assertTrue(result.candidates[0].is_selected)
            check.assertEqual(len(result.candidates[1].matches), 2)
            check.assertEqual(collection.get(include=["metadatas", "documents"]), before)
            with check.assertRaises(CandidateSearchError):
                search_candidates(collection, ["위험"], selected, Encoder(), model="wrong-model")
        elif mode == "write_then_fail":
            collection.upsert(ids=["fixture"], documents=["test only"],
                              embeddings=[[1.0] + [0.0] * (DIMENSION - 1)])
            raise RuntimeError("simulated test failure")
        elif mode == "reopen":
            check.assertEqual(collection.get(ids=["fixture"])["ids"], ["fixture"])
        else:
            raise ValueError("Unknown worker mode")
    print(f"CHROMA_CHECKS_PASSED {mode}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
