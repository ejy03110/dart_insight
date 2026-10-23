"""Automatic collection/indexing integration and session-context guards."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dart_insight.ui.analysis import followup_history
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION


class Ux05IntegrationTests(unittest.TestCase):
    def test_actual_runtime_parser_database_graph_and_resume_in_child(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('ux05_integration_worker.py')),directory],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('UX05:',result.stdout)

    def test_followup_includes_own_analysis_and_conversation_without_other_items(self):
        analyses={'a':{'content':'A analysis'},'b':{'content':'B private analysis'}}
        histories={'a':[{'role':'user','content':'A question','rcept_no':'a'},{'role':'assistant','content':'A answer','rcept_no':'a'}]}
        history=followup_history('a',analyses,histories)
        self.assertEqual([t['role'] for t in history],['user','assistant','user','assistant'])
        self.assertEqual(history[0]['content'],ANALYSIS_QUESTION)
        self.assertEqual(history[1]['content'],'A analysis')
        self.assertNotIn('B private',str(history))
        self.assertEqual(len(histories['a']),2)

    def test_actual_click_prepares_missing_index_and_enables_contextual_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('ux05_ui_worker.py')),directory],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('UX05 UI:',result.stdout)


class LiveProbeContractTests(unittest.TestCase):
    def test_missing_credentials_reports_blocked_without_provider_calls_or_db_creation(self):
        import json, os
        with tempfile.TemporaryDirectory() as directory:
            report=Path(directory)/'report.json'
            env={**os.environ,'DART_API_KEY':'','GMS_API_KEY':'','OPENAI_API_KEY':''}
            result=subprocess.run([sys.executable,'scripts/verify_ux05_live.py','--output',str(report)],env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,2,result.stderr)
            data=json.loads(report.read_text())
            self.assertEqual(data['status'],'blocked')
            self.assertEqual(data['runs'],[])
            self.assertEqual(data['blocker'],'missing_credentials')
            self.assertFalse(list(Path(directory).rglob('*.sqlite3')))


import test_rag_ui as fixture
import test_recent_ui as recent_fixture
from streamlit.testing.v1 import AppTest


class Ux05UiBoundTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def test_failed_briefing_does_not_fan_out_or_retry_without_user_action(self):
        recent_fixture.RecentUiTests.build_recent(self)
        self.service.ask.side_effect=RuntimeError('private-failure')
        app=AppTest.from_file(fixture.APP).run()
        self.service.ask.assert_called_once()
        selected=self.service.ask.call_args.args[0]
        self.assertEqual(app.session_state['briefing_receipt'],selected.rcept_no)
        self.assertFalse(app.session_state['analyses'])
        self.assertTrue(any('다른 공시를 자동으로 추가 분석하지 않습니다' in item.value for item in app.info))
        app.run();app.run()
        self.service.ask.assert_called_once()
        app.button(key='recent_home_005930_'+selected.rcept_no).click().run()
        self.assertTrue(app.chat_input[0].proto.disabled)
        self.service.ask.assert_called_once()
        self.service.ask.side_effect=self.answer
        app.button(key='retry_analysis_'+selected.rcept_no).click().run()
        self.assertEqual(self.service.ask.call_count,2)
        self.assertFalse(app.chat_input[0].proto.disabled)

    def test_authentication_failure_shows_reason_without_raw_provider_text(self):
        from dart_insight.rag.llm import LLMError
        self.service.ask.side_effect=LLMError('private-provider-response',category='authentication',status=401)
        app=fixture.start_analysis()
        self.assertTrue(any('인증 설정' in item.value for item in app.error))
        self.assertFalse(any('private-provider-response' in item.value for item in app.error))
        self.assertTrue(app.chat_input[0].proto.disabled)
        self.service.ask.assert_called_once()
