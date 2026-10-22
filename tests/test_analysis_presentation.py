"""Public analysis renders full answers without technical progress or empty scroll boxes."""
import unittest
from unittest.mock import patch
import streamlit as st
import test_rag_ui as fixture


class AnalysisPresentationTests(unittest.TestCase):
    setUp = fixture.RagUiTests.setUp
    answer = fixture.RagUiTests.answer

    def test_generic_spinner_without_technical_stage_output(self):
        messages = ['GMS 분석 중', 'LangGraph 실행 중', 'BGE-M3 임베딩 중', 'XML 처리 중']
        def answer(selected, question, progress, **kwargs):
            for message in messages:
                progress(message)
            return self.answer(selected, question, progress)
        self.service.ask.side_effect = answer
        app = fixture.start_analysis()
        with patch('streamlit.status', wraps=st.status) as status, patch('streamlit.spinner', wraps=st.spinner) as spinner, patch('streamlit.write', wraps=st.write) as write:
            app.button(key='run_analysis').click().run()
            status.assert_not_called()
            self.assertTrue(any(call.args[0] == '분석 중입니다. 잠시만 기다려 주세요.' for call in spinner.call_args_list))
            for message in messages:
                self.assertFalse(any(message in str(call.args) for call in write.call_args_list))
        self.assertFalse(any(e.label=='처리 환경 상세' for e in app.expander))
        self.assertEqual(len(app.exception),0)

    def test_empty_chat_has_no_messages_or_scrolling_container_and_long_answer_is_complete(self):
        app=fixture.start_analysis()
        self.assertEqual(len(app.chat_message),0)
        self.assertEqual(len(app.chat_input),1)
        self.assertFalse(any(getattr(b.proto,'autoscroll',False) for b in app.get('flex_container')))
        text='\n\n'.join(f'{i}. 긴 답변 내용 [근거 1]' for i in range(150))
        self.service.ask.side_effect=lambda selected, question, progress, **kwargs: {**self.answer(selected,question,progress),'answer':text}
        app.chat_input[0].set_value('긴 답변 요청').run()
        self.assertEqual([m.name for m in app.chat_message],['user','assistant'])
        self.assertTrue(any(m.value==text for m in app.markdown))
        self.assertEqual(app.session_state['histories'][fixture.FIRST.rcept_no][1]['content'],text)
        self.assertFalse(any(getattr(b.proto,'autoscroll',False) for b in app.get('flex_container')))
        self.assertFalse(any('conversation_messages' in str(b.proto) for b in app.get('flex_container')))

    def test_failure_keeps_previous_analysis_without_leaking_exception(self):
        app=fixture.start_analysis()
        app.button(key='run_analysis').click().run()
        previous=dict(app.session_state['analyses'][fixture.FIRST.rcept_no])
        self.service.ask.side_effect=RuntimeError('test-only-sensitive-error-token GMS LangGraph XML')
        app.button(key='run_analysis').click().run()
        self.assertEqual(app.session_state['analyses'][fixture.FIRST.rcept_no],previous)
        self.assertFalse(any('test-only-sensitive-error-token' in e.value or 'GMS' in e.value for e in app.error))
        self.assertTrue(any('이전 결과와 대화는 유지' in e.value for e in app.error))
