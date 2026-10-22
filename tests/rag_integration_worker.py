"""Real Chroma/SQLite/LangGraph checks in a child, so Windows closes all handles on exit."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dart_insight.config import Settings
from dart_insight.data.models import Disclosure
from dart_insight.data.documents import CollectedDocument
from dart_insight.data.collection_ledger import CollectionLedger
from dart_insight.rag.retrieval import retrieve
from dart_insight.services.rag_service import RagService
from dart_insight.vectorstore.metadata import MetadataDocument, open_metadata_collection, index_metadata
from dart_insight.vectorstore.documents import open_document_collection, index_documents
from dart_insight.vectorstore.embeddings import DIMENSION, LOCAL_MODEL


class Encoder:
    model_name = LOCAL_MODEL

    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += len(texts)
        return [[1.0, 0.0] + [0.0] * (DIMENSION - 2) if '위험' in t else
                [0.0, 1.0] + [0.0] * (DIMENSION - 2) for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class LLM:
    def __init__(self, include_other=False):
        self.include_other = include_other
        self.extract_calls = self.select_calls = 0

    def extract(self, selected, question):
        self.extract_calls += 1
        return ['위험']

    def json(self, system, payload, validate):
        if 'evidence' in payload:
            number = next((r['number'] for r in payload['evidence'] if r['source_type'] == 'opendart_document'), 1)
            return validate({'answer': f'사업 위험이 명시되어 있습니다. [근거 {number}]', 'supported': True})
        self.select_calls += 1
        return validate({'selected_ids': ['20250101000002'] if self.include_other else []})


class Source:
    def __init__(self, other):
        self.other, self.search_calls, self.collect_calls = other, 0, []
        self.fail_once = False

    def search(self, query, selected):
        from dart_insight.data.documents import DocumentCandidate
        self.search_calls += 1
        return [DocumentCandidate(selected.rcept_no, self.other.rcept_no, self.other.report_nm,
                                  self.other.source_url, self.other.corp_code)]

    def collect(self, candidate):
        self.collect_calls.append(candidate.document_rcept_no)
        if self.fail_once and candidate.document_rcept_no == self.other.rcept_no:
            self.fail_once = False
            raise RuntimeError('simulated download failure')
        return CollectedDocument(**candidate.to_dict(), text='사업 위험: 환율 변동 위험이 있습니다.\n' * 20,
                                 collected_at='2026-10-22T00:00:00+09:00')


def disclosure(receipt):
    return Disclosure('00126380', '삼성전자', '005930', '사업보고서', receipt, '2025-01-01', '정기공시',
                      f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}', '2026-10-22T00:00:00+09:00')


def main(directory):
    check = unittest.TestCase()
    settings = Settings(data_dir=directory, chunk_size=80, chunk_overlap=20, top_k=4)
    enc = Encoder()
    metadata = open_metadata_collection(directory / 'db', LOCAL_MODEL)
    docs = open_document_collection(directory / 'db', LOCAL_MODEL)
    selected, other = disclosure('20250101000001'), disclosure('20250101000002')
    for item in (selected, other):
        index_metadata([MetadataDocument(f'meta:{item.rcept_no}', item.to_index_text(), item.to_dict())],
                       metadata, enc, model=LOCAL_MODEL)
    ledger = CollectionLedger(directory / 'ledger.sqlite3')
    source, llm = Source(other), LLM()
    service = RagService(settings, metadata, docs, enc, ledger, source, llm)
    progress = []
    result = service.ask(selected, '위험은?', progress.append)
    check.assertIn('[근거', result['answer'])
    check.assertEqual(result['evidence'][0].id, f'meta:{selected.rcept_no}')
    check.assertTrue(any(e.source_type == 'opendart_document' for e in result['evidence']))
    check.assertEqual(source.collect_calls, [selected.rcept_no])  # selected forced despite LLM exclusion
    check.assertTrue(any(other.rcept_no in p['rejected_ids'] for p in result['audit']['selection']))
    check.assertTrue(any('생성' in p for p in progress))
    count, calls = docs.count(), (source.search_calls, len(source.collect_calls), llm.extract_calls, llm.select_calls)
    # Reopen ledger/service to exercise persistence, not an in-memory completion cache.
    service = RagService(settings, metadata, docs, enc, CollectionLedger(ledger.path), source, llm)
    repeated = service.ask(selected, '위험은?')
    check.assertEqual(calls, (source.search_calls, len(source.collect_calls), llm.extract_calls, llm.select_calls))
    check.assertEqual(docs.count(), count)
    check.assertEqual(ledger.get(selected.rcept_no, '위험').state, 'indexed')
    check.assertEqual(repeated['citations'], result['citations'])
    check.assertEqual(len(retrieve(metadata, docs, selected, '위험', enc, replace(settings, top_k=1))), 1)
    check.assertGreater(len(retrieve(metadata, docs, selected, '위험', enc, settings)), 1)
    # Same original under another selected item has a disjoint ID namespace.
    original = next(iter(ledger.documents_for_item(selected.rcept_no).values()))
    shared = replace(original, item_rcept_no=other.rcept_no)
    index_documents([shared], docs, enc, settings)
    check.assertEqual(docs.count(), count * 2)
    scoped = retrieve(metadata, docs, other, '위험', enc, settings)
    check.assertTrue(all(other.rcept_no in e.id for e in scoped))
    # Configuration changes re-chunk existing originals and remove only this item's stale version.
    index_documents([original], docs, enc, replace(settings, chunk_size=150, chunk_overlap=10))
    changed = docs.get(where={'item_rcept_no': selected.rcept_no}, include=[])
    check.assertNotEqual(len(changed['ids']), count)
    check.assertEqual(len(docs.get(where={'item_rcept_no': other.rcept_no}, include=[])['ids']), count)
    # Failed indexing keeps collected; successful partial downloads persist across service recreation.
    second_source, second_llm = Source(other), LLM(include_other=True)
    second_source.fail_once = True
    second_ledger = CollectionLedger(directory / 'retry.sqlite3')
    retry = RagService(settings, metadata, docs, enc, second_ledger, second_source, second_llm)
    with check.assertRaises(RuntimeError):
        retry.prepare(selected, '새로운 질문')
    check.assertEqual(second_ledger.get(selected.rcept_no, '위험').state, 'pending')
    check.assertIn(selected.rcept_no, CollectionLedger(second_ledger.path).documents_for_item(selected.rcept_no))
    retry = RagService(settings, metadata, docs, enc, CollectionLedger(second_ledger.path), second_source, second_llm)
    retry.prepare(selected, '새로운 질문')
    check.assertEqual(second_source.collect_calls.count(selected.rcept_no), 1)
    # One external search per distinct generated query, including the pinned receipt.
    check.assertEqual(second_source.search_calls, 2)
    check.assertEqual(second_ledger.get(selected.rcept_no, '위험').state, 'indexed')
    # Multi-document intent can retain the additional selected original, while
    # the earlier single-document case explicitly rejected it. No forced quota.
    plan = second_ledger.plan(selected.rcept_no, '위험')
    check.assertIn(other.rcept_no, plan['selected_ids'])
    check.assertTrue(docs.get(where={'$and': [{'item_rcept_no': selected.rcept_no},
                                            {'document_rcept_no': other.rcept_no}]}, include=[])['ids'])
    # A failed embedding must not promote a newly collected query to indexed.
    class FailingEncoder(Encoder):
        def embed_query(self, text):
            return [1.0] + [0.0] * (DIMENSION - 1)

        def embed_documents(self, texts):
            raise RuntimeError('simulated embedding failure')

    index_ledger = CollectionLedger(directory / 'index-retry.sqlite3')
    index_source = Source(other)
    new_settings = replace(settings, chunk_size=81)
    failing = RagService(new_settings, metadata, docs, FailingEncoder(), index_ledger, index_source, LLM())
    with check.assertRaises(RuntimeError):
        failing.prepare(selected, '색인 재시도')
    check.assertEqual(index_ledger.get(selected.rcept_no, '위험').state, 'collected')
    successful = RagService(new_settings, metadata, docs, enc, index_ledger, index_source, LLM())
    successful.prepare(selected, '색인 재시도')
    check.assertEqual(index_ledger.get(selected.rcept_no, '위험').state, 'indexed')
    check.assertEqual(index_source.collect_calls.count(selected.rcept_no), 1)
    # New settings restore a wiped document collection even when ledger says indexed.
    docs.delete(where={'item_rcept_no': selected.rcept_no})
    service.prepare(selected, '위험은?')
    check.assertGreater(len(docs.get(where={'item_rcept_no': selected.rcept_no}, include=[])['ids']), 0)
    # Same generated query, new comparison question: reuse the external search
    # but reselect cached candidates, downloading only newly chosen originals.
    shared_docs = open_document_collection(directory / 'question-scoped-db', LOCAL_MODEL)
    shared_ledger = CollectionLedger(directory / 'question-scoped.sqlite3')
    shared_source, shared_llm = Source(other), LLM()
    shared_service = RagService(settings, metadata, shared_docs, enc, shared_ledger, shared_source, shared_llm)
    shared_service.prepare(selected, '이 공시만 분석')
    searches = shared_source.search_calls
    shared_llm.include_other = True
    compared = shared_service.prepare(selected, '두 공시를 비교')
    check.assertEqual(shared_source.search_calls, searches)
    check.assertEqual(shared_source.collect_calls.count(selected.rcept_no), 1)
    check.assertEqual(shared_source.collect_calls.count(other.rcept_no), 1)
    check.assertTrue(any(other.rcept_no in p['selected_ids'] for p in compared['selection']))
    selections = shared_llm.select_calls
    shared_service.prepare(selected, '두 공시를 비교')
    check.assertEqual(shared_llm.select_calls, selections)
    # Old completed base-query rows are preserved, not overwritten/downgraded.
    check.assertEqual([d.document_rcept_no for d in shared_ledger.get(selected.rcept_no, '위험').documents],
                      [selected.rcept_no])
    print('RAG_INTEGRATION_PASSED: persistent Chroma, ledger resume, LangGraph, citations, isolation, rechunking')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
