"""Minimal MMR math, single-pool search and missing-index safety checks."""
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from dart_insight.rag.mmr import mmr_indices, compare_search
from dart_insight.vectorstore.embeddings import LOCAL_MODEL, PROFILE


class MmrTests(unittest.TestCase):
    def test_diversity_penalty_changes_second_result_and_lambda_one_is_similarity(self):
        vectors=[[1,0],[0.99,0.01],[0.6,0.8]]
        self.assertEqual(mmr_indices([1,0],vectors,k=3,lambda_mult=1),[0,1,2])
        self.assertEqual(mmr_indices([1,0],vectors,k=3,lambda_mult=0.3),[0,2,1])

    def test_ties_empty_pool_and_small_pool_are_deterministic(self):
        self.assertEqual(mmr_indices([1,0],[],k=5),[])
        self.assertEqual(mmr_indices([1,0],[[1,0],[1,0]],k=5),[0,1])
        self.assertEqual(mmr_indices([1,0],[[1,0],[1,0],[0,1]],k=2,lambda_mult=0),[0,2])

    def test_invalid_parameters_and_vectors_fail_without_silent_truncation(self):
        for kwargs in ({'k':0},{'lambda_mult':-0.1},{'lambda_mult':math.nan}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                mmr_indices([1,0],[[1,0]],**kwargs)
        for query,vectors in [([0,0],[[1,0]]),([1,0],[[1]]),([1,0],[[math.inf,0]])]:
            with self.subTest(vectors=vectors),self.assertRaises(ValueError):
                mmr_indices(query,vectors)

    def test_comparison_reuses_one_query_vector_and_candidate_pool_without_writes(self):
        collection=Mock()
        collection.metadata={'embedding_model':LOCAL_MODEL,'embedding_profile':PROFILE,'hnsw:space':'cosine'}
        collection.count.return_value=3
        vectors=[[1,0]+[0]*1022,[0.99,0.01]+[0]*1022,[0.6,0.8]+[0]*1022]
        collection.get.return_value={'ids':['a'],'embeddings':[vectors[0]]}
        collection.query.return_value={'ids':[['a','b','c']], 'embeddings':[vectors],
            'documents':[['fixture']*3], 'metadatas':[[{'document_rcept_no':v} for v in ['same','same','other']]]}
        encoder=Mock(model_name=LOCAL_MODEL)
        encoder.embed_query.return_value=vectors[0]
        result=compare_search(collection,encoder,'동일 질문',k=2,fetch_k=3,lambda_mult=0.3,where={'corp_code':'test'})
        self.assertEqual([r['id'] for r in result['similarity']],['a','b'])
        self.assertEqual([r['id'] for r in result['mmr']],['a','c'])
        self.assertEqual(result['similarity_metrics']['unique_documents'],1)
        self.assertEqual(result['mmr_metrics']['unique_documents'],2)
        self.assertLess(result['mmr_metrics']['mean_pairwise_cosine'],result['similarity_metrics']['mean_pairwise_cosine'])
        encoder.embed_query.assert_called_once_with('동일 질문')
        collection.query.assert_called_once()
        self.assertEqual(collection.query.call_args.kwargs['where'],{'corp_code':'test'})
        collection.upsert.assert_not_called();collection.delete.assert_not_called()
        encoder.embed_documents.assert_not_called()

    def test_cli_missing_index_does_not_create_db_or_write_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/'report.md'
            result=subprocess.run([sys.executable,'scripts/compare_mmr.py','--db-dir',str(root/'missing'),
                                   '--query','사업 위험','--output',str(output)],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertEqual(json.loads(result.stdout)['reason'],'existing_index_missing')
            self.assertFalse((root/'missing').exists())
            self.assertFalse(output.exists())
