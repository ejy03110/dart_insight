"""Home briefing failure causes, persistent safe diagnostics and bounded calls."""
import json
import unittest

from streamlit.testing.v1 import AppTest
import test_rag_ui as fixture
import test_recent_ui as recent_fixture
from dart_insight.opendart.client import DartError
from dart_insight.rag.llm import LLMError
from dart_insight.services.original_documents import parse_document_zip
from dart_insight.ui.analysis_diagnostics import failure, home_retry_allowed


class DiagnosticTests(unittest.TestCase):
    def diagnostic(self, exc, phase='parse'):
        return failure(exc, phase=phase, origin='home', receipt=fixture.FIRST.rcept_no)

    def test_real_opendart_missing_file_response_is_distinct_from_corrupt_zip(self):
        for content,reason,code in [
            (b'<result><status>014</status><message>SECRET</message></result>','original_unavailable','014'),
            (b'not a document SECRET','parse_failed','document_format')]:
            with self.subTest(reason=reason):
                try:
                    parse_document_zip(content)
                except DartError as exc:
                    diagnostic=self.diagnostic(exc)
                else:
                    self.fail('invalid document accepted')
                self.assertEqual((diagnostic['reason'],diagnostic['dart_code']),(reason,code))
                self.assertFalse(diagnostic['retryable'])
                self.assertNotIn('SECRET',json.dumps(diagnostic))

    def test_known_causes_and_untrusted_error_attributes_are_allowlisted(self):
        from dart_insight.vectorstore.metadata import MetadataIndexError
        cases=[(DartError('SECRET','document_empty'),'original_empty'),
               (DartError('SECRET','document_identity'),'identity_mismatch'),
               (DartError('SECRET','network'),'transient_service'),
               (DartError('SECRET','020'),'rate_limit'),
               (LLMError('SECRET',category='authentication',status=401),'authentication'),
               (LLMError('SECRET',category='timeout'),'transient_service'),
               (LLMError('SECRET',category='grounding'),'answer_validation'),
               (MetadataIndexError('SECRET'),'index_configuration')]
        for exc,reason in cases:
            with self.subTest(reason=reason):
                diagnostic=self.diagnostic(exc)
                self.assertEqual(diagnostic['reason'],reason)
                self.assertNotIn('SECRET',json.dumps(diagnostic))
        hostile=RuntimeError('SECRET');hostile.code='SECRET';hostile.category='SECRET';hostile.status='SECRET'
        diagnostic=failure(hostile,phase='SECRET',origin='SECRET',receipt='SECRET')
        self.assertNotIn('SECRET',json.dumps(diagnostic))
        self.assertEqual(diagnostic['reason'],'unexpected')
        self.assertEqual(self.diagnostic(PermissionError('SECRET'))['exception'],'PermissionError')

    def test_only_transient_failures_have_one_home_retry(self):
        transient=self.diagnostic(LLMError('SECRET',category='server'))
        self.assertTrue(home_retry_allowed(transient,1))
        self.assertFalse(home_retry_allowed(transient,2))
        self.assertFalse(home_retry_allowed(self.diagnostic(DartError('SECRET','014')),1))
        self.assertFalse(home_retry_allowed(self.diagnostic(LLMError('SECRET',category='citation')),1))


class HomeBriefingTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def start(self):
        recent_fixture.RecentUiTests.build_recent(self)
        return AppTest.from_file(fixture.APP,default_timeout=15).run()

    def test_success_keeps_citations_and_survives_rerender_and_detail_without_gms_duplicate(self):
        app=self.start()
        receipt=app.session_state['briefing_receipt']
        self.assertIn(receipt,app.session_state['analyses'])
        self.assertTrue(app.session_state['analyses'][receipt]['citations'])
        app.run();app.run()
        app.button(key='recent_home_005930_'+receipt).click().run()
        self.assertFalse(app.chat_input[0].proto.disabled)
        self.service.ask.assert_called_once()
        self.assertEqual(len(app.exception),0)

    def test_missing_original_reason_survives_rerender_without_calls_or_fake_result(self):
        def missing(selected,question,progress,**kwargs):
            progress('원문 다운로드 중');progress('XML 처리 중')
            return parse_document_zip(b'<result><status>014</status><message>SECRET</message></result>')
        self.service.ask.side_effect=missing
        with self.assertLogs('dart_insight.operations',level='WARNING') as logs:
            app=self.start()
            receipt=app.session_state['briefing_receipt']
            diagnostic=dict(app.session_state['analysis_failures'][receipt])
            app.run();app.run()
        self.assertEqual((diagnostic['phase'],diagnostic['dart_code'],diagnostic['origin']),('parse','014','home'))
        self.assertEqual(diagnostic,app.session_state['analysis_failures'][receipt])
        self.assertNotIn(receipt,app.session_state['analyses'])
        self.assertTrue(any('원문 파일을 제공하지 않습니다' in n.value for n in app.info))
        self.assertFalse(any(b.key=='retry_analysis_'+receipt for b in app.button))
        self.service.ask.assert_called_once()
        self.assertEqual(sum('analysis_failed' in line for line in logs.output),1)
        self.assertNotIn('SECRET',str(logs.output))
        self.assertIn(diagnostic['diagnostic_id'],str(logs.output))

    def test_transient_failure_retry_is_explicit_and_limited_to_one(self):
        self.service.ask.side_effect=LLMError('SECRET',category='timeout')
        app=self.start();receipt=app.session_state['briefing_receipt']
        app.run();app.run()
        self.service.ask.assert_called_once()
        app.button(key='retry_analysis_'+receipt).click().run()
        self.assertEqual(self.service.ask.call_count,2)
        app.run();app.run()
        self.assertEqual(self.service.ask.call_count,2)
        self.assertFalse(any(b.key=='retry_analysis_'+receipt for b in app.button))
        self.assertEqual(app.session_state['analysis_run_counts'][receipt],2)
        self.assertNotIn(receipt,app.session_state['analyses'])

    def test_transient_retry_success_is_reused_without_automatic_recollection(self):
        self.service.ask.side_effect=LLMError('SECRET',category='server',status=503)
        app=self.start();receipt=app.session_state['briefing_receipt']
        self.service.ask.side_effect=self.answer
        app.button(key='retry_analysis_'+receipt).click().run()
        self.assertIn(receipt,app.session_state['analyses'])
        self.assertNotIn(receipt,app.session_state['analysis_failures'])
        app.run();app.run()
        self.assertEqual(self.service.ask.call_count,2)

    def test_auth_and_validation_failures_do_not_offer_home_retry_or_hide_reason(self):
        recent_fixture.RecentUiTests.build_recent(self)
        for category,reason in [('authentication','authentication'),('citation','answer_validation')]:
            with self.subTest(category=category):
                self.service.ask.reset_mock()
                self.service.ask.side_effect=LLMError('SECRET',category=category)
                app=AppTest.from_file(fixture.APP).run()
                receipt=app.session_state['briefing_receipt']
                app.run()
                self.assertEqual(app.session_state['analysis_failures'][receipt]['reason'],reason)
                self.assertFalse(any(b.key=='retry_analysis_'+receipt for b in app.button))
                self.assertNotIn('SECRET',str([n.value for n in app.error]+[n.value for n in app.info]))
                self.service.ask.assert_called_once()

    def test_unknown_or_empty_answer_is_failed_not_a_completed_briefing(self):
        self.service.ask.side_effect=None
        self.service.ask.return_value={'answer':'제공된 문서에서 확인되지 않습니다.','citations':[]}
        app=self.start();receipt=app.session_state['briefing_receipt']
        self.assertEqual(app.session_state['analysis_attempts'][receipt],'failed')
        self.assertEqual(app.session_state['analysis_failures'][receipt]['reason'],'insufficient_evidence')
        self.assertNotIn(receipt,app.session_state['analyses'])
        app.run()
        self.service.ask.assert_called_once()

    def test_cached_detail_unknown_is_preserved_but_is_not_a_completed_home_briefing(self):
        app=self.start();receipt=app.session_state['briefing_receipt']
        cached={'content':'제공된 문서에서 확인되지 않습니다.','citations':[]}
        app.session_state['analyses']={receipt:cached}
        app.run();app.run()
        self.service.ask.assert_called_once()
        self.assertEqual(app.session_state['analyses'][receipt],cached)
        self.assertEqual(app.session_state['analysis_failures'][receipt]['reason'],'insufficient_evidence')
        self.assertFalse(any('현재 세션에 저장된 분석입니다' in n.value for n in app.caption))

    def test_legacy_running_state_is_diagnosed_without_silent_restart(self):
        self.service.ask.side_effect=LLMError('SECRET',category='timeout')
        app=self.start();receipt=app.session_state['briefing_receipt']
        app.session_state['analysis_attempts']={receipt:'running'}
        app.session_state['analysis_failures']={}
        app.run();app.run()
        self.assertEqual(app.session_state['analysis_failures'][receipt]['reason'],'interrupted')
        self.service.ask.assert_called_once()


class HomeRuntimeIntegrationTests(unittest.TestCase):
    def test_actual_home_runtime_parser_index_graph_and_missing_original_in_child(self):
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('home_briefing_worker.py')),directory],
                                  capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('HOME:',result.stdout)
