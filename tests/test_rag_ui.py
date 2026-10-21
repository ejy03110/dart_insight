"""Full Streamlit interaction checks with injected store/provider boundaries."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from dart_insight.config import Settings
from dart_insight.data.models import Disclosure

APP = str(Path(__file__).resolve().parent.parent / 'app.py')
FIRST = Disclosure('00126380', '삼성전자', '005930', '사업보고서', '20250101000001', '2025-01-01',
                   '정기공시', 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000001', '')
SECOND = replace(FIRST, rcept_no='20250101000002', report_nm='감사보고서',
                 source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000002')


class RagUiTests(unittest.TestCase):
    def setUp(self):
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)
        self.stack = self.enterContext(ExitStack())
        settings = Settings(dart_api_key='test-only-dart', openai_api_key='test-only-gms')
        metadata = Mock()
        metadata.count.return_value = 2
        self.stack.enter_context(patch('dart_insight.config.load_settings', return_value=settings))
        self.stack.enter_context(patch('dart_insight.services.runtime.open_store', return_value=(metadata, Mock())))
        self.stack.enter_context(patch('dart_insight.services.runtime.list_disclosures', return_value=[FIRST, SECOND]))
        self.runtime = self.stack.enter_context(patch('dart_insight.services.runtime.Runtime'))
        self.service = self.runtime.return_value.__enter__.return_value
        self.service.ask.side_effect = self.answer
        self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network')))

    def answer(self, selected, question, progress):
        progress('선별 공시 원문 수집 중')
        progress('근거 기반 답변 생성 중')
        return {'answer': f'{selected.report_nm} 답변 [근거 1]', 'citations': [
            {'number': 1, 'id': 'meta:' + selected.rcept_no, 'text': selected.to_index_text(),
             'title': selected.report_nm, 'source': selected.source_url, 'source_type': 'opendart_metadata'}],
            'audit': {'queries': ['사업보고서'], 'selection': []}}

    def test_chat_switch_back_and_analysis_keep_item_histories_and_citations(self):
        app = AppTest.from_file(APP, default_timeout=10).run()
        self.assertEqual(len(app.exception), 0)
        self.service.ask.assert_not_called()
        app.chat_input[0].set_value('첫 질문').run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn('[근거 1]', app.session_state['histories'][FIRST.rcept_no][1]['content'])
        app.selectbox(key='chosen_receipt').select(SECOND.rcept_no).run()
        self.assertEqual(len(app.chat_message), 0)
        app.chat_input[0].set_value('두번째 질문').run()
        self.assertEqual(len(app.session_state['histories'][SECOND.rcept_no]), 2)
        app.selectbox(key='chosen_receipt').select(FIRST.rcept_no).run()
        self.assertEqual(len(app.chat_message), 2)
        app.button[1].click().run()  # connection submit is button 0
        self.assertIn('위험 요인', self.service.ask.call_args.args[1])
        self.assertEqual(len(app.session_state['histories'][FIRST.rcept_no]), 4)
        self.assertTrue(any('외부 문서' in str(x.value) or '항목 메타데이터' in str(x.value) for x in app.markdown))

    def test_uncited_answer_notice_and_safe_failure(self):
        app = AppTest.from_file(APP, default_timeout=10).run()
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
            app = AppTest.from_file(APP, default_timeout=10).run()
            app.text_area[0].set_value('감사보고서만').run()
            app.button[2].click().run()  # sidebar discovery submit follows main buttons
            self.assertEqual(len(app.exception), 0)
            discover.assert_called_once()
            result_button = next(b for b in app.button if b.key == 'result_' + SECOND.rcept_no)
            result_button.click().run()
            self.assertEqual(app.session_state['chosen_receipt'], SECOND.rcept_no)
