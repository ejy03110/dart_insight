"""UI-only navigation must not invoke providers or discard receipt-scoped state."""
import unittest
import test_rag_ui as fixture
from streamlit.testing.v1 import AppTest


class UiShellTests(unittest.TestCase):
    setUp = fixture.RagUiTests.setUp
    answer = fixture.RagUiTests.answer

    def test_company_search_and_navigation_do_not_call_rag(self):
        app = AppTest.from_file(fixture.APP).run()
        for query in ['005930', '삼성전자', '없는기업', '']:
            app.text_input(key='company_search').set_value(query).run()
            self.assertEqual(len(app.exception), 0)
            if query == '없는기업':
                self.assertTrue(any('조건에 맞는 저장된 공시가 없습니다' in i.value for i in app.info))
            else:
                self.assertEqual(app.session_state['chosen_receipt'], fixture.FIRST.rcept_no)
        for section in ['공시 탐색', '분석 기록', '홈']:
            app.radio(key='ui_section').set_value(section).run()
            self.assertEqual(len(app.exception), 0)
        self.service.ask.assert_not_called()
        self.runtime.assert_not_called()
        self.assertEqual(app.selectbox(key='chosen_receipt').options,
                         ['삼성전자 · 사업보고서 · 2025-01-01', '삼성전자 · 감사보고서 · 2025-01-01'])
        self.assertTrue(any(link.proto.url == fixture.FIRST.source_url for link in app.get('link_button')))

    def test_sidebar_record_restores_analysis_and_multiturn_without_call(self):
        app = AppTest.from_file(fixture.APP).run()
        app.button(key='run_analysis').click().run()
        app.chat_input[0].set_value('첫 질문').run()
        app.chat_input[0].set_value('그럼 쉽게 설명해줘').run()
        saved = list(app.session_state['histories'][fixture.FIRST.rcept_no])
        analysis = dict(app.session_state['analyses'][fixture.FIRST.rcept_no])
        app.selectbox(key='chosen_receipt').select(fixture.SECOND.rcept_no).run()
        calls = self.service.ask.call_count
        app.radio(key='ui_section').set_value('분석 기록').run()
        app.button(key='history_' + fixture.FIRST.rcept_no).click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.session_state['chosen_receipt'], fixture.FIRST.rcept_no)
        self.assertEqual(app.session_state['histories'][fixture.FIRST.rcept_no], saved)
        self.assertEqual(app.session_state['analyses'][fixture.FIRST.rcept_no], analysis)
        self.assertEqual(len(app.chat_message), 4)
        self.assertEqual(self.service.ask.call_count, calls)
