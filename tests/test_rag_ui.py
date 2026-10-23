"""Full Streamlit interaction checks with injected store/provider boundaries."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from admin_fixture import configure_admin, login

from dart_insight.config import Settings
from dart_insight.data.models import Disclosure

APP = str(Path(__file__).resolve().parent.parent / 'app.py')
FIRST = Disclosure('00126380', '삼성전자', '005930', '사업보고서', '20250101000001', '2025-01-01',
                   '정기공시', 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000001', '')
SECOND = replace(FIRST, rcept_no='20250101000002', report_nm='감사보고서',
                 source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000002')


def explore(app):
    app.radio(key='ui_section').set_value('공시 탐색').run()
    return app


def open_disclosure(app, receipt):
    # A direct analysis URL tests the same automatic detail behavior without
    # constraining navigation to the obsolete source-selection radio.
    app.query_params.update(v='analysis', r=receipt)
    app.run()
    return app


def start_analysis():
    app = AppTest.from_file(APP, default_timeout=10)
    app.query_params.update(v='analysis', r=FIRST.rcept_no)
    return app.run()


class RagUiTests(unittest.TestCase):
    def setUp(self):
        configure_admin(self)
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)
        self.stack = self.enterContext(ExitStack())
        settings = Settings(dart_api_key='test-only-dart', openai_api_key='test-only-gms')
        metadata = Mock()
        metadata.count.return_value = 2
        self.stack.enter_context(patch('dart_insight.config.load_settings', return_value=settings))
        self.stack.enter_context(patch('dart_insight.services.runtime.open_store', return_value=(metadata, Mock())))
        self.stack.enter_context(patch('dart_insight.services.runtime.list_disclosures', return_value=[FIRST, SECOND]))
        self.stack.enter_context(patch('dart_insight.services.runtime.analysis_readiness', return_value=(True, 'AI 분석 가능 · 본문 색인 확인')))
        from dart_insight.services.recent_disclosures import RecentDisclosureService
        self.stack.enter_context(patch('dart_insight.ui.recent.recent_service', return_value=RecentDisclosureService(Settings())))
        self.runtime = self.stack.enter_context(patch('dart_insight.services.runtime.Runtime'))
        self.service = self.runtime.return_value.__enter__.return_value
        self.service.ask.side_effect = self.answer
        self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network')))

    def answer(self, selected, question, progress, **kwargs):
        progress('선별 공시 원문 수집 중')
        progress('근거 기반 답변 생성 중')
        return {'answer': f'{selected.report_nm} 답변 [근거 1]', 'citations': [
            {'number': 1, 'id': 'meta:' + selected.rcept_no, 'text': selected.to_index_text(),
             'title': selected.report_nm, 'source': selected.source_url, 'source_type': 'opendart_metadata'}],
            'audit': {'queries': ['사업보고서'], 'selection': []}}

    def test_chat_switch_back_and_analysis_keep_item_histories_and_citations(self):
        app = start_analysis()
        self.assertEqual(len(app.exception), 0)
        self.service.ask.assert_called_once()
        app.chat_input[0].set_value('첫 질문').run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn('[근거 1]', app.session_state['histories'][FIRST.rcept_no][1]['content'])
        open_disclosure(app, SECOND.rcept_no)
        self.assertEqual(len(app.chat_message), 0)
        app.chat_input[0].set_value('두번째 질문').run()
        self.assertEqual(len(app.session_state['histories'][SECOND.rcept_no]), 2)
        open_disclosure(app, FIRST.rcept_no)
        self.assertEqual(len(app.chat_message), 2)
        self.assertTrue(any('위험 요인' in c.args[1] for c in self.service.ask.call_args_list))
        self.assertEqual(len(app.session_state['histories'][FIRST.rcept_no]), 2)
        self.assertIn(FIRST.rcept_no, app.session_state['analyses'])
        self.assertEqual(len(app.chat_message), 2)
        self.assertTrue(any('외부 문서' in str(x.value) or '항목 메타데이터' in str(x.value) for x in app.caption))

    def test_uncited_answer_notice_and_safe_failure(self):
        app = start_analysis()
        self.service.ask.side_effect = None
        self.service.ask.return_value = {'answer': '제공된 문서에서 확인되지 않습니다.', 'citations': []}
        app.chat_input[0].set_value('없는 내용').run()
        self.assertTrue(any('인용된 근거가 없습니다' in n.value for n in app.info))
        self.service.ask.side_effect = RuntimeError('test-only-gms sensitive traceback')
        app.chat_input[0].set_value('재질문').run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('처리 실패' in e.value for e in app.error))
        self.assertNotIn('test-only-gms', str([e.value for e in app.error]))
        self.assertEqual(len(app.session_state['histories'][FIRST.rcept_no]), 2)

    def test_discovery_selection_moves_to_matched_disclosure(self):
        with patch('dart_insight.services.discovery.discover', return_value=[{'metadata': SECOND.to_dict()}]) as discover:
            app = start_analysis()
            explore(app)
            app.text_area[0].set_value('감사보고서만').run()
            app.button(key='FormSubmitter:discovery-조건에 맞는 공시 검색').click().run()
            self.assertEqual(len(app.exception), 0)
            discover.assert_called_once()
            result_button = next(b for b in app.button if b.key == 'result_' + SECOND.rcept_no)
            result_button.click().run()
            self.assertEqual(app.session_state['chosen_receipt'], SECOND.rcept_no)

    def test_analysis_is_separate_cached_and_restored_without_rerunning_during_chat(self):
        from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
        app = start_analysis()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.chat_message), 0)
        self.assertEqual(app.session_state['histories'][FIRST.rcept_no], [])
        first_analysis = app.session_state['analyses'][FIRST.rcept_no].copy()
        app.chat_input[0].set_value('후속 질문').run()
        self.assertEqual(app.session_state['analyses'][FIRST.rcept_no], first_analysis)
        self.assertEqual(len(app.chat_message), 2)
        self.assertEqual(sum(call.args[1] == ANALYSIS_QUESTION for call in self.service.ask.call_args_list), 1)
        count = self.service.ask.call_count
        open_disclosure(app, SECOND.rcept_no)
        self.assertEqual(len(app.chat_message), 0)
        self.assertIn(SECOND.rcept_no, app.session_state['analyses'])
        open_disclosure(app, FIRST.rcept_no)
        self.assertEqual(app.session_state['analyses'][FIRST.rcept_no], first_analysis)
        self.assertEqual(len(app.chat_message), 2)
        self.assertEqual(self.service.ask.call_count, count + 1)

    def test_duplicate_source_link_is_shown_once_with_both_chunk_numbers(self):
        def answer(selected, question, progress):
            result = self.answer(selected, question, progress)
            first = {**result['citations'][0], 'number': 2, 'id': 'doc:chunk2', 'source_type': 'opendart_document'}
            second = {**first, 'number': 3, 'id': 'doc:chunk3'}
            result['citations'] = [first, second]
            result['answer'] = '두 주장 [근거 2] [근거 3]'
            return result
        self.service.ask.side_effect = answer
        app = start_analysis()
        app.chat_input[0].set_value('질문').run()
        self.assertEqual(sum(m.value == FIRST.source_url for m in app.markdown), 2)  # one per answer, grouped within each
        self.assertTrue(any('[근거 2]' in c.value for c in app.caption))
        self.assertTrue(any('[근거 3]' in c.value for c in app.caption))
        self.assertFalse(any('doc:chunk2' in c.value for c in app.caption))
        self.assertFalse(any('doc:chunk3' in c.value for c in app.caption))

    def test_connection_date_form_and_collection_command_use_same_dates(self):
        from datetime import date
        with patch('dart_insight.opendart.client.DartClient') as client:
            api = client.return_value.__enter__.return_value
            api.resolve_company.return_value = {'corp_code': FIRST.corp_code}
            api.list_disclosures.return_value = []
            app = start_analysis()
            login(app)
            app.toggle(key='diagnostic_open').set_value(True).run()
            app.date_input[0].set_value(date(2024, 2, 1))
            app.date_input[1].set_value(date(2025, 3, 11))
            app.run()
            self.assertIn('--begin 2024-02-01 --end 2025-03-11', app.code[0].value)
            api.list_disclosures.assert_not_called()
            app.button(key='connect_dart').click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn('--begin 2024-02-01 --end 2025-03-11', app.code[0].value)
            self.assertEqual(api.list_disclosures.call_args.args[1:3], (date(2024, 2, 1), date(2025, 3, 11)))

    def test_year_search_explains_default_without_irrelevant_numeric_body_notice(self):
        with patch('dart_insight.services.discovery.discover', return_value=[]):
            app = start_analysis()
            explore(app)
            app.text_area[0].set_value('삼성전자 2035년 사업보고서')
            app.button(key='FormSubmitter:discovery-조건에 맞는 공시 검색').click().run()
            self.assertTrue(any('제출연도' in c.value for c in app.caption))
            self.assertTrue(any('조건에 맞는 결과가 없습니다' in i.value for i in app.info))
            self.assertFalse(any('본문 수치' in i.value for i in app.info))

    def test_recovery_notes_and_supported_copy_ui_are_visible_after_analysis_and_rerun(self):
        def answer(selected, question, progress):
            result = self.answer(selected, question, progress)
            result['audit']['parse_notes'] = ['XML #1: < TV 시장점유율 추이 > 문자 보존 복구. 삭제한 본문 없음.']
            return result
        self.service.ask.side_effect = answer
        app = start_analysis()
        self.assertTrue(any('제목 표기' in w.value for w in app.warning))
        self.assertTrue(any('< TV 시장점유율 추이 >' in t.value for t in app.text))
        self.assertTrue(any('사업보고서 답변 [근거 1]' in code.value for code in app.code))
        calls = self.service.ask.call_count
        app.run()
        self.assertEqual(self.service.ask.call_count, calls)
        self.assertTrue(any('제목 표기' in w.value for w in app.warning))

    def test_query_fallback_warning_is_preserved_with_analysis_without_reexecution(self):
        def answer(selected, question, progress):
            result = self.answer(selected, question, progress)
            result['audit']['query_fallback'] = {'category': 'timeout', 'http_status': None}
            return result
        self.service.ask.side_effect = answer
        app = start_analysis()
        self.assertTrue(any('원래 질문' in w.value for w in app.warning))
        calls = self.service.ask.call_count
        app.run()
        self.assertEqual(self.service.ask.call_count, calls)
        self.assertTrue(any('원래 질문' in w.value for w in app.warning))

    def test_failed_stage_is_visible_and_previous_analysis_survives(self):
        from dart_insight.opendart.client import DartError
        app = start_analysis()
        previous = dict(app.session_state['analyses'][FIRST.rcept_no])
        def fail(selected, question, progress):
            progress('XML 처리 중')
            raise DartError('공시 XML #1: 안전한 구문 오류 안내', 'document_xml')
        self.service.ask.side_effect = fail
        app.chat_input[0].set_value('실패 질문').run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('분석을 완료하지 못했습니다' in e.value for e in app.error))
        self.assertFalse(any('XML' in e.value for e in app.error))
        self.assertEqual(app.session_state['analyses'][FIRST.rcept_no], previous)

    def test_multiturn_context_is_scoped_and_single_input_follows_analysis(self):
        app = start_analysis()
        app.chat_input[0].set_value('첫 질문').run()
        self.assertEqual(len(app.chat_input), 1)
        self.assertEqual([m.name for m in app.chat_message], ['user', 'assistant'])
        app.chat_input[0].set_value('그럼 쉽게 설명해줘').run()
        forwarded = self.service.ask.call_args.kwargs['history']
        self.assertEqual([t['role'] for t in forwarded], ['user', 'assistant'])
        self.assertEqual(forwarded[0]['content'], '첫 질문')
        self.assertEqual(len(app.chat_message), 4)
        self.assertEqual(len(app.chat_input), 1)
        calls = self.service.ask.call_count
        app.run()
        self.assertEqual(self.service.ask.call_count, calls)
        self.assertEqual(len(app.chat_message), 4)
        # No fixed-height/scrolling conversation block; messages grow with the page.
        scrolling = [b for b in app.get('flex_container') if getattr(b.proto, 'autoscroll', False)]
        self.assertFalse(scrolling)
        self.assertFalse(any(getattr(b.proto, "height", 0) == 450 for b in app.get("flex_container")))
        open_disclosure(app, SECOND.rcept_no)
        app.chat_input[0].set_value('다른 공시 첫 질문').run()
        self.assertNotIn('history', self.service.ask.call_args.kwargs)
        self.assertEqual(len(app.chat_message), 2)
        open_disclosure(app, FIRST.rcept_no)
        self.assertEqual(len(app.chat_message), 4)
        self.assertEqual(len(app.chat_input), 1)
