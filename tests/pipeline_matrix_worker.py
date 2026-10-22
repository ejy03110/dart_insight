"""Child-process actual parser/DB/graph matrix; HTTP and BGE are injected test doubles."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.rag_integration_worker import Encoder
from dart_insight.config import Settings
from dart_insight.data.models import Disclosure
from dart_insight.data.collection_ledger import CollectionLedger
from dart_insight.opendart.client import DartError
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.pipeline import UNKNOWN
from dart_insight.services.original_documents import OpenDartSource, parse_document_zip
from dart_insight.services.rag_service import RagService
from dart_insight.vectorstore.documents import open_document_collection
from dart_insight.vectorstore.metadata import MetadataDocument, index_metadata, open_metadata_collection


def archive(text):
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        z.writestr('original.xml', text.encode('utf-8'))
    return output.getvalue()


def main(root):
    check = unittest.TestCase()
    # Company/period metadata below are test fixtures, not an official-code lookup.
    rows = [Disclosure(code, company, stock, title, receipt, day, kind,
                       f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}', '')
            for code, company, stock, title, receipt, day, kind in [
                ('00000001', '삼성전자', '005930', '사업보고서 (2024.12)', '20250311001085', '2025-03-11', '정기공시'),
                ('00000002', '현대자동차', '005380', '분기보고서', '20250515000001', '2025-05-15', '정기공시'),
                ('00000003', '금양', '001570', '단기차입금증가결정(자율공시)', '20251203800556', '2025-12-03', '기타공시')]]
    real_zip = (Path(__file__).parent / 'fixtures/opendart/20251203800556.zip').read_bytes()
    originals = {
        rows[0].rcept_no: archive('<DOCUMENT>' + '<P>합성 사업보고서: 환율 변동 위험 및 R&D.</P>' * 600 +
                                  '<P>< TV 시장점유율 추이 ></P></DOCUMENT>'),
        rows[1].rcept_no: archive('<DOC><P>합성 분기보고서: 원자재 위험.</P></DOC>'),
        rows[2].rcept_no: archive('<html><body><div>불완전 본문</body></html>')}
    settings = Settings(data_dir=root, chunk_size=300, chunk_overlap=40, top_k=4, request_interval_seconds=0)
    encoder = Encoder()
    metadata = open_metadata_collection(root / 'db', settings.embedding_model)
    documents = open_document_collection(root / 'db', settings.embedding_model)
    index_metadata([MetadataDocument('meta:' + row.rcept_no, row.to_index_text(), row.to_dict()) for row in rows],
                   metadata, encoder, model=settings.embedding_model)
    dart = Mock()
    dart.list_disclosures.side_effect = lambda code, *args, **kwargs: [r for r in rows if r.corp_code == code]
    dart._request.side_effect = lambda endpoint, params: Mock(content=originals[params['rcept_no']])
    source = OpenDartSource(settings, dart)
    session = Mock()
    mode = 'normal'
    query_transient = True
    def post(url, **kwargs):
        nonlocal query_transient
        payload = json.loads(kwargs['json']['messages'][1]['content'])
        if mode == 'auth':
            return Mock(status_code=401, json=lambda: {'error': {'code': 'invalid_api_key'}})
        if 'disclosure' in payload:
            if query_transient:
                query_transient = False
                return Mock(status_code=429, json=lambda: {})
            value = {'queries': ['위험']}
        elif 'candidates' in payload:
            value = {'selected_ids': []}  # Selected item is pinned by the production service.
        else:
            number, doc = next((e['number'], e) for e in payload['evidence'] if e['source_type'] == 'opendart_document')
            value = {'answer': '문서 발췌: ' + doc['text'][:50] + f' [근거 {number}]', 'supported': True}
            if mode == 'bad_citation':
                value['answer'] = '허위 인용 [근거 999999]'
            elif mode == 'unsupported':
                value = {'answer': 'ignored unsupported prose', 'supported': False}
        # Whole JSON fences are a lossless presentation variant, not a schema bypass.
        content = '```json\n' + json.dumps(value, ensure_ascii=False) + '\n```'
        return Mock(status_code=200, json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
    session.post.side_effect = post
    llm = GmsClient(replace(settings, openai_api_key='test-only-no-real-credential'), session)
    ledger = CollectionLedger(root / 'ledger.sqlite3')
    service = RagService(settings, metadata, documents, encoder, ledger, source, llm)
    with patch('time.sleep'):
        for row in rows[:2]:
            result = service.ask(row, '위험을 알려주세요')
            check.assertTrue(result['citations'])
            check.assertTrue(all(c['id'].startswith(('meta:' + row.rcept_no, 'doc:' + row.rcept_no)) for c in result['citations']))
            requests_before, encoder_before = dart._request.call_count, encoder.calls
            stable = documents.count()
            repeat = service.ask(row, '위험을 알려주세요')
            check.assertEqual(repeat['citations'], result['citations'])
            check.assertEqual(dart._request.call_count, requests_before)
            check.assertEqual(encoder.calls - encoder_before, 1)  # Query only, no document re-embedding.
            check.assertEqual(documents.count(), stable)
        # Corrupt third filing cannot save a partial body or alter earlier companies.
        stable = documents.count()
        with check.assertRaises(DartError):
            service.ask(rows[2], '위험을 알려주세요')
        check.assertEqual(documents.count(), stable)
        check.assertEqual(ledger.get(rows[2].rcept_no, '위험').state, 'pending')
        check.assertEqual(ledger.documents_for_item(rows[2].rcept_no), {})
        originals[rows[2].rcept_no] = real_zip
        actual = service.ask(rows[2], '위험을 알려주세요')
        stored = ledger.documents_for_item(rows[2].rcept_no)[rows[2].rcept_no]
        check.assertEqual(stored.text, parse_document_zip(real_zip))
        check.assertIn('HTML 빈 요소 14건', stored.parse_notes[0])
        check.assertTrue(actual['citations'])
        check.assertTrue(all(c['id'].startswith(('meta:' + rows[2].rcept_no, 'doc:' + rows[2].rcept_no)) for c in actual['citations']))
        requests_before = dart._request.call_count
        service.ask(rows[2], '위험을 알려주세요')
        check.assertEqual(dart._request.call_count, requests_before)
        stable = documents.count()
        mode = 'bad_citation'
        with check.assertRaises(LLMError):
            service.ask(rows[1], '다른 근거 질문')
        check.assertEqual(documents.count(), stable)
        mode = 'normal'
        check.assertTrue(service.ask(rows[1], '다른 근거 질문')['citations'])
        check.assertEqual(dart._request.call_count, requests_before)
        mode = 'unsupported'
        missing = service.ask(rows[0], '문서에 없는 사실 질문')
        check.assertEqual(missing['answer'], UNKNOWN)
        check.assertEqual(missing['citations'], [])
        mode = 'auth'
        calls = session.post.call_count
        with check.assertRaises(LLMError):
            service.ask(rows[0], '인증 오류 재현 질문')
        check.assertEqual(session.post.call_count - calls, 1)
        check.assertEqual(documents.count(), stable)
        mode = 'normal'
        check.assertTrue(service.ask(rows[0], '인증 오류 재현 질문')['citations'])
        # Refuse legacy/corrupt cross-company ledger content without changing the real index.
        from dart_insight.data.documents import CollectedDocument
        from dart_insight.services.original_documents import selected_candidate
        bad_ledger = CollectionLedger(root / 'wrong-company.sqlite3')
        values = selected_candidate(rows[1]).to_dict()
        values['corp_code'] = rows[0].corp_code
        bad_doc = CollectedDocument(**values, text='잘못된 기업 원문 테스트', collected_at='2026-10-22T00:00:00+00:00')
        bad_ledger.ensure_pending(rows[1].rcept_no, '위험')
        bad_ledger.mark_collected(rows[1].rcept_no, '위험', [bad_doc])
        bad_service = RagService(settings, metadata, documents, encoder, bad_ledger, source, llm)
        with check.assertRaises(DartError) as mismatch:
            bad_service.prepare(rows[1], '기업 식별자 검증 질문')
        check.assertEqual(mismatch.exception.code, 'document_identity')
        check.assertEqual(documents.count(), stable)
        check.assertEqual(bad_ledger.get(rows[1].rcept_no, '위험').state, 'collected')
        check.assertEqual(metadata.count(), 3)
    print('PIPELINE_MATRIX_PASSED: actual ZIP, 3 company/type fixtures, GMS boundary, retry, rejection, reuse, recovery')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
