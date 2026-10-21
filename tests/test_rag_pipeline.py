"""Required workflow checks; all provider calls are mocked, real graph executes."""
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

import requests

from dart_insight.config import Settings, ConfigurationError
from dart_insight.data.documents import CollectedDocument
from dart_insight.data.models import Disclosure
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.pipeline import UNKNOWN, build_graph, validate_answer
from dart_insight.rag.retrieval import Evidence
from dart_insight.rag.prompts.selection import selected_ids
from dart_insight.services.discovery import discover
from dart_insight.services.original_documents import BundleSource, OpenDartSource, parse_document_zip, selected_candidate
from dart_insight.ui.chat import save_turn
from dart_insight.opendart.client import DartError

ITEM = '20250101000001'
SELECTED = Disclosure('00126380', '삼성전자', '005930', '사업보고서', ITEM, '2025-01-01',
                      '정기공시', f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={ITEM}', '2026-10-22T00:00:00Z')
DOC = CollectedDocument(**selected_candidate(SELECTED).to_dict(), text='실제 문서가 아닌 테스트 전용 본문',
                        collected_at='2026-10-22T00:00:00Z')


def zipped(text, encoding='utf-8'):
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('../original.xml', text.encode(encoding))  # must never extract paths
    return output.getvalue()


class OriginalTests(unittest.TestCase):
    def test_utf8_legacy_korean_and_table_text(self):
        for encoding in ('utf-8', 'euc-kr'):
            content = zipped(f'<?xml version="1.0" encoding="{encoding}"?><DOCUMENT><TITLE>사업보고서</TITLE>'
                             '<TABLE><TR><TD>환율</TD><TD>위험</TD></TR></TABLE></DOCUMENT>', encoding)
            self.assertEqual(parse_document_zip(content), '사업보고서\n환율\n위험')

    def test_entity_attack_corruption_empty_and_error_envelope(self):
        for value in (b'garbage', zipped('<bad>'), zipped('<DOC/>'),
                      zipped('<!DOCTYPE x [<!ENTITY a "injected">]><DOC>&a;</DOC>'),
                      b'<result><status>014</status><message>secret</message></result>'):
            with self.subTest(value=value[:10]), self.assertRaises(DartError) as caught:
                parse_document_zip(value)
            self.assertNotIn('secret', str(caught.exception))

    def test_external_search_uses_all_types_and_no_page_limit_then_downloads_receipt(self):
        client = Mock()
        client.list_disclosures.return_value = [SELECTED]
        source = OpenDartSource(Settings(), client)
        result = source.search('사업보고서', SELECTED)
        self.assertEqual(len(result), 1)
        self.assertEqual(client.list_disclosures.call_count, 10)
        self.assertIsNone(client.list_disclosures.call_args.kwargs['max_pages'])
        source.search('다른 검색어', SELECTED)
        self.assertEqual(client.list_disclosures.call_count, 10)
        response = client._request.return_value
        response.content = zipped('<DOC>원문 본문</DOC>')
        self.assertEqual(source.collect(result[0]).text, '원문 본문')
        client._request.assert_called_once_with('document.xml', {'rcept_no': ITEM})
        response.close.assert_called_once()

    def test_real_bundle_contract_rebinds_selected_item_and_requires_original(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bundle.json'
            path.write_text(json.dumps({'schema_version': 1, 'documents': [DOC.to_dict()]}), encoding='utf-8')
            source = BundleSource(path)
            other = replace(SELECTED, rcept_no='20250101000002',
                            source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000002')
            candidate = source.search('사업보고서', other)[0]
            self.assertEqual(source.collect(candidate).item_rcept_no, other.rcept_no)
            with self.assertRaises(DartError):
                source.collect(selected_candidate(other))
            path.write_text('{"schema_version":1,"documents":[]}', encoding='utf-8')
            with self.assertRaises(DartError):
                BundleSource(path)


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [Evidence('meta:x', '제목', '사업보고서', SELECTED.source_url, 'opendart_metadata'),
                         Evidence('doc:x', '원문 내용', '공시 원문', SELECTED.source_url, 'opendart_document')]

    def test_only_actually_cited_evidence_displayed_in_first_use_order(self):
        result = validate_answer({'answer': '주장 [근거 2] 반복 [근거 2]', 'supported': True}, self.evidence)
        self.assertEqual([c['number'] for c in result['citations']], [2])
        self.assertEqual(result['citations'][0]['source_type'], 'opendart_document')

    def test_invalid_or_missing_citations_and_extra_fields_rejected(self):
        for answer in ('무근거 주장', '주장 [근거 0]', '주장 [근거 3]', '주장 [근거 1] [근거 없는 번호]'):
            with self.assertRaises(ValueError):
                validate_answer({'answer': answer, 'supported': True}, self.evidence)
        with self.assertRaises(ValueError):
            validate_answer({'answer': 'x', 'supported': True, 'url': 'fake'}, self.evidence)

    def test_unsupported_answer_is_canonical_and_has_no_citation(self):
        result = validate_answer({'answer': '근거 없는 답변 [근거 9]', 'supported': False}, self.evidence)
        self.assertEqual(result, {'answer': UNKNOWN, 'citations': []})

    def test_actual_langgraph_nodes_and_empty_evidence_skip_generation(self):
        llm = Mock()
        progress = []
        graph = build_graph(lambda q: [], llm, progress.append)
        self.assertEqual(set(graph.get_graph().nodes), {'__start__', 'retrieve', 'generate', '__end__'})
        result = graph.invoke({'question': '문서 밖 질문'})
        self.assertEqual(result['answer'], UNKNOWN)
        llm.json.assert_not_called()
        self.assertEqual(len(progress), 2)

    def test_grounded_graph_passes_types_and_numbers_to_generation(self):
        llm = Mock()
        llm.json.side_effect = lambda system, payload, validate: validate({'answer': '제목 [근거 1]', 'supported': True})
        result = build_graph(lambda q: self.evidence, llm).invoke({'question': '제목?'})
        self.assertEqual(len(result['citations']), 1)
        self.assertEqual(llm.json.call_args.args[1]['evidence'][1]['source_type'], 'opendart_document')

    def test_histories_stay_separate_when_switching_back(self):
        histories = {}
        save_turn(histories, ITEM, 'A 질문', {'answer': 'A 답변', 'citations': []})
        save_turn(histories, '20250101000002', 'B 질문', {'answer': 'B 답변', 'citations': []})
        save_turn(histories, ITEM, 'A 추가', {'answer': 'A 추가 답변', 'citations': []})
        self.assertEqual(len(histories[ITEM]), 4)
        self.assertEqual(len(histories['20250101000002']), 2)
        self.assertNotIn('B 질문', str(histories[ITEM]))


class GmsTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.settings = Settings(openai_api_key='test-only-key', request_interval_seconds=0)
        self.client = GmsClient(self.settings, self.session)

    def response(self, content, status=200):
        return Mock(status_code=status, json=lambda: {'choices': [{'message': {'content': content}}]})

    def test_auth_schema_redirect_policy_and_bounded_json_retry(self):
        self.session.post.side_effect = [self.response('secret malformed'), self.response('{"selected_ids":[]}')]
        result = self.client.json('prompt', {}, lambda v: selected_ids(v, {ITEM}))
        self.assertEqual(result, [])
        self.assertEqual(self.session.post.call_count, 2)
        kwargs = self.session.post.call_args.kwargs
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], 20)
        self.assertEqual(kwargs['json']['model'], 'gpt-4.1-mini')
        self.assertNotIn('secret malformed', str(kwargs['json']))

    def test_missing_key_unknown_id_and_malformed_json_exhaustion(self):
        with self.assertRaises(ConfigurationError):
            GmsClient(Settings(), self.session).json('x', {}, lambda v: v)
        self.session.post.assert_not_called()
        for content in ('bad', '{"selected_ids":["invented"]}'):
            self.session.reset_mock()
            self.session.post.return_value = self.response(content)
            with self.assertRaises(LLMError):
                self.client.json('x', {}, lambda v: selected_ids(v, {ITEM}))
            self.assertEqual(self.session.post.call_count, 3)

    def test_transport_and_auth_errors_never_echo_secrets(self):
        self.session.post.side_effect = requests.Timeout('test-only-key')
        with self.assertRaises(LLMError) as caught:
            self.client.json('x', {}, lambda v: v)
        self.assertNotIn('test-only-key', str(caught.exception))
        self.session.post.side_effect = None
        self.session.post.return_value = self.response('', 403)
        self.session.post.reset_mock()
        with self.assertRaises(LLMError):
            self.client.json('x', {}, lambda v: v)
        self.assertEqual(self.session.post.call_count, 1)

    @patch('dart_insight.rag.llm.time.sleep')
    def test_transient_server_retries_are_bounded(self, sleep):
        self.session.post.return_value = self.response('', 503)
        with self.assertRaises(LLMError):
            self.client.json('x', {}, lambda v: v)
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


class DiscoveryTests(unittest.TestCase):
    @patch('dart_insight.services.discovery.query_metadata')
    def test_vector_candidates_are_filtered_by_independent_condition_prompt(self, query):
        query.return_value = [{'metadata': {'rcept_no': ITEM, 'report_nm': '사업보고서'}},
                              {'metadata': {'rcept_no': '20250101000002', 'report_nm': '감사보고서'}}]
        llm = Mock()
        llm.json.side_effect = lambda prompt, data, validate: validate({'selected_ids': [ITEM]})
        result = discover(Mock(), '사업보고서만', Mock(), Settings(), llm)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['metadata']['rcept_no'], ITEM)
        self.assertIn('확인 불가능', llm.json.call_args.args[0])
        llm.json.side_effect = lambda prompt, data, validate: validate({'selected_ids': []})
        self.assertEqual(discover(Mock(), '본문으로만 확인 가능한 조건', Mock(), Settings(), llm), [])

    @patch('dart_insight.services.discovery.query_metadata', return_value=[])
    def test_empty_results_skip_llm_and_empty_condition_rejected(self, query):
        llm = Mock()
        self.assertEqual(discover(Mock(), '조건', Mock(), Settings(), llm), [])
        llm.json.assert_not_called()
        with self.assertRaises(ValueError):
            discover(Mock(), ' ', Mock(), Settings(), llm)


class PersistentIntegrationTests(unittest.TestCase):
    def test_real_db_graph_retry_isolation_and_rechunking_in_child(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('rag_integration_worker.py')), temp],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('RAG_INTEGRATION_PASSED', result.stdout)

class ExportAndSettingsTests(unittest.TestCase):
    def test_export_preserves_sources_and_refuses_overwrite_without_api_call(self):
        from dart_insight.export_documents import main
        with tempfile.TemporaryDirectory() as temp, \
             patch('dart_insight.export_documents.load_settings', return_value=Settings()), \
             patch('dart_insight.export_documents.open_store', return_value=(Mock(), Mock())), \
             patch('dart_insight.export_documents.list_disclosures', return_value=[SELECTED]), \
             patch('dart_insight.export_documents.OpenDartSource') as source:
            output = Path(temp) / 'originals.json'
            source.return_value.collect.return_value = DOC
            with patch.object(sys, 'argv', ['export', '--receipt', ITEM, '--output', str(output)]):
                self.assertEqual(main(), 0)
                saved = output.read_text(encoding='utf-8')
                self.assertEqual(json.loads(saved)['documents'][0]['source_url'], SELECTED.source_url)
                source.return_value.collect.reset_mock()
                self.assertEqual(main(), 1)
                source.return_value.collect.assert_not_called()
                self.assertEqual(output.read_text(encoding='utf-8'), saved)

    def test_failed_download_does_not_create_bundle(self):
        from dart_insight.export_documents import main
        with tempfile.TemporaryDirectory() as temp, \
             patch('dart_insight.export_documents.load_settings', return_value=Settings()), \
             patch('dart_insight.export_documents.open_store', return_value=(Mock(), Mock())), \
             patch('dart_insight.export_documents.list_disclosures', return_value=[SELECTED]), \
             patch('dart_insight.export_documents.OpenDartSource') as source:
            output = Path(temp) / 'missing.json'
            source.return_value.collect.side_effect = DartError('원문이 없습니다.')
            with patch.object(sys, 'argv', ['export', '--receipt', ITEM, '--output', str(output)]):
                self.assertEqual(main(), 1)
            self.assertFalse(output.exists())
            source.return_value.close.assert_called_once()

    def test_cloud_alias_keeps_legacy_key_compatible_without_repr_leak(self):
        import os
        from dart_insight.config import load_settings
        with patch('dart_insight.config.dotenv_values', return_value={}), patch.dict(os.environ, {}, clear=True):
            legacy = load_settings({'OPENAI_API_KEY': 'legacy-test-only'})
            self.assertEqual(legacy.openai_api_key, 'legacy-test-only')
            current = load_settings({'OPENAI_API_KEY': 'legacy-test-only', 'GMS_API_KEY': 'alias-test-only'})
            self.assertEqual(current.openai_api_key, 'alias-test-only')
            self.assertNotIn('alias-test-only', repr(current))
