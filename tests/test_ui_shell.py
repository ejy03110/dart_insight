"""Route transitions preserve receipt-scoped state without implicit provider calls."""
from dataclasses import replace
import unittest
from unittest.mock import patch
import test_rag_ui as fixture
from streamlit.testing.v1 import AppTest


class UiShellTests(unittest.TestCase):
    setUp = fixture.RagUiTests.setUp
    answer = fixture.RagUiTests.answer

    def test_company_search_and_navigation_do_not_call_rag(self):
        app = AppTest.from_file(fixture.APP).run()
        self.assertEqual(len(app.chat_input), 0)
        self.assertFalse(any('색인' in m.value for m in app.markdown))
        self.assertFalse(any('827' in m.value for m in app.markdown))
        self.assertTrue(any('투자 조언이 아닙니다' in c.value for c in app.caption))
        self.assertEqual(len(app.metric), 0)
        for query in ['005930', '삼성전자', '없는기업', '']:
            app.text_input(key='home_search').set_value(query).run()
            self.assertEqual(len(app.exception), 0)
        app.button(key='company_005930').click().run()
        self.assertEqual(app.session_state['ui_page'], '공시 탐색')
        self.assertEqual(app.text_input(key='api_company').value, '삼성전자')
        self.assertFalse(any('research-hero' in m.value and '<section' in m.value for m in app.markdown))
        fixture.open_disclosure(app, fixture.FIRST.rcept_no)
        self.assertEqual(app.session_state['ui_page'], '공시 분석')
        self.assertEqual(len(app.text_input), 0)
        self.assertEqual(len(app.selectbox), 0)
        self.assertEqual(len(app.chat_input), 1)
        self.assertTrue(any(link.proto.url == fixture.FIRST.source_url for link in app.get('link_button')))
        self.service.ask.assert_called_once()

    def test_sidebar_record_restores_analysis_and_multiturn_without_call(self):
        app = fixture.start_analysis()
        app.chat_input[0].set_value('첫 질문').run()
        app.chat_input[0].set_value('그럼 쉽게 설명해줘').run()
        saved = list(app.session_state['histories'][fixture.FIRST.rcept_no])
        analysis = dict(app.session_state['analyses'][fixture.FIRST.rcept_no])
        fixture.open_disclosure(app, fixture.SECOND.rcept_no)
        calls = self.service.ask.call_count
        app.radio(key='ui_section').set_value('분석 기록').run()
        self.assertEqual(len(app.chat_input), 0)
        self.assertEqual(len(app.text_input), 0)
        app.button(key='record_' + fixture.FIRST.rcept_no).click().run()
        self.assertEqual(app.session_state['chosen_receipt'], fixture.FIRST.rcept_no)
        self.assertEqual(app.session_state['histories'][fixture.FIRST.rcept_no], saved)
        self.assertEqual(app.session_state['analyses'][fixture.FIRST.rcept_no], analysis)
        self.assertEqual(len(app.chat_message), 4)
        self.assertEqual(self.service.ask.call_count, calls)
        app.radio(key='ui_section').set_value('홈').run()
        app.button(key='history_' + fixture.FIRST.rcept_no).click().run()
        self.assertEqual(len(app.chat_message), 4)
        self.assertEqual(self.service.ask.call_count, calls)

    def test_empty_records_and_no_duplicate_controls(self):
        app = AppTest.from_file(fixture.APP).run()
        app.radio(key='ui_section').set_value('분석 기록').run()
        self.assertTrue(any(i.value == '아직 분석 기록이 없습니다.' for i in app.info))
        self.assertEqual(len(app.text_input), 0)
        self.assertEqual(len(app.chat_input), 0)
        self.assertFalse(any(b.key.startswith('history_') for b in app.button if b.key))
        self.assertEqual(len(app.date_input), 0)
        self.service.ask.assert_not_called()

    def test_explore_filters_persist_but_do_not_reset_selected_receipt(self):
        third = replace(fixture.FIRST, corp_name='금양', stock_code='001570', corp_code='00999999',
                        rcept_no='20250201000003', rcept_dt='2025-02-01', disclosure_type='주요사항보고')
        with patch('dart_insight.services.runtime.list_disclosures', return_value=[fixture.FIRST,fixture.SECOND,third]):
            app = fixture.start_analysis()
            chosen = app.session_state['chosen_receipt']
            fixture.explore(app)
            self.assertFalse(any(r.key == '_explore_scope' for r in app.radio))
            app.text_input(key='api_company').set_value('001570')
            app.date_input(key='api_begin').set_value(__import__('datetime').date(2025,2,1))
            app.date_input(key='api_end').set_value(__import__('datetime').date(2025,2,1))
            app.button(key='api_submit').click().run()
            self.assertEqual([b.key for b in app.button if b.key and b.key.startswith('open_')], ['open_' + third.rcept_no])
            app.radio(key='ui_section').set_value('홈').run()
            fixture.explore(app)
            self.assertEqual(app.text_input(key='api_company').value, '001570')
            self.assertEqual(app.date_input(key='api_begin').value, __import__('datetime').date(2025,2,1))
            self.assertEqual(app.session_state['chosen_receipt'], chosen)
            self.assertEqual(self.service.ask.call_count, 1)

    def test_unstructured_answer_is_preserved_without_inventing_sections(self):
        text = '형식 없는 원문 답변입니다.\n\n- 그대로 보존합니다. [근거 1]'
        self.service.ask.side_effect = lambda *args, **kw: {**self.answer(*args[:3]), 'answer':text}
        app = fixture.start_analysis()
        self.assertEqual(app.session_state['analyses'][fixture.FIRST.rcept_no]['content'],text)
        self.assertTrue(any(m.value == text for m in app.markdown))
        self.assertFalse(any(h.value == '위험 요인' for h in app.subheader))

    def test_diagnostic_navigation_does_not_lose_conversation_or_call_provider(self):
        app = fixture.start_analysis()
        app.chat_input[0].set_value('질문').run()
        saved = list(app.session_state['histories'][fixture.FIRST.rcept_no])
        calls = self.service.ask.call_count
        with patch('dart_insight.opendart.client.DartClient') as client:
            fixture.login(app)
            app.toggle(key='diagnostic_open').set_value(True).run()
            client.assert_not_called()
            self.assertEqual(len(app.chat_input),0)
            app.toggle(key='diagnostic_open').set_value(False).run()
        self.assertEqual(app.session_state['histories'][fixture.FIRST.rcept_no],saved)
        self.assertEqual(len(app.chat_message),2)
        self.assertEqual(self.service.ask.call_count,calls)
