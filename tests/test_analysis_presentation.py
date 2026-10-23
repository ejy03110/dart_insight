"""Public analysis renders full answers without technical progress or empty scroll boxes."""
import unittest
from unittest.mock import patch
import streamlit as st
import test_rag_ui as fixture


class AnalysisPresentationTests(unittest.TestCase):
    setUp = fixture.RagUiTests.setUp
    answer = fixture.RagUiTests.answer

    def test_real_progress_without_technical_stage_output(self):
        messages = ['GMS 분석 중', 'LangGraph 실행 중', 'BGE-M3 임베딩 중', 'XML 처리 중']
        def answer(selected, question, progress, **kwargs):
            for message in messages:
                progress(message)
            return self.answer(selected, question, progress)
        self.service.ask.side_effect = answer
        with patch('streamlit.status', wraps=st.status) as status, patch('streamlit.spinner', wraps=st.spinner) as spinner, patch('streamlit.write', wraps=st.write) as write:
            app = fixture.start_analysis()
            status.assert_called_once()
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
        previous=dict(app.session_state['analyses'][fixture.FIRST.rcept_no])
        self.service.ask.side_effect=RuntimeError('test-only-sensitive-error-token GMS LangGraph XML')
        app.chat_input[0].set_value('실패 질문').run()
        self.assertEqual(app.session_state['analyses'][fixture.FIRST.rcept_no],previous)
        self.assertFalse(any('test-only-sensitive-error-token' in e.value or 'GMS' in e.value for e in app.error))
        self.assertTrue(any('이전 결과와 대화는 유지' in e.value for e in app.error))

    def test_progress_reports_only_emitted_stages_and_errors(self):
        def failed(selected,question,progress,**kwargs):
            progress('원문 다운로드 중')
            progress('XML 처리 중')
            raise RuntimeError('secret-not-for-display')
        self.service.ask.side_effect=failed
        app=fixture.start_analysis()
        self.assertEqual(app.status[0].state,'error')
        text=' '.join(m.value for m in app.markdown)
        self.assertIn('공시 원문을 수집',text)
        self.assertIn('공시 원문을 읽고',text)
        self.assertNotIn('답변을 작성',text)
        self.assertNotIn('secret-not-for-display',text)

    def test_chat_input_is_after_all_saved_turns(self):
        app=fixture.start_analysis()
        app.chat_input[0].set_value('첫 질문').run()
        app.chat_input[0].set_value('두 번째 질문').run()
        # Inspect rendered order recursively rather than merely checking state.
        elements=list(app.main.__iter__())
        sequence=[node.type for node in elements if node.type in ('chat_message','chat_input')]
        self.assertEqual(sequence,['chat_message','chat_message','chat_message','chat_message','chat_input'])

    def test_citation_number_displays_its_actual_document_title(self):
        app=fixture.start_analysis()
        citations=app.session_state['analyses'][fixture.FIRST.rcept_no]['citations']
        for citation in citations:
            self.assertTrue(any(f"[근거 {citation['number']}] {citation['title']}" in c.value for c in app.caption))
