"""Authentication is checked server-side even when UI/session routing is forged."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from admin_fixture import configure_admin, login, PASSWORD
import test_rag_ui as fixture
from dart_insight.ui import admin_auth
from dart_insight.ui.diagnostics import render_diagnostics
from dart_insight.config import Settings


class GrantTests(unittest.TestCase):
    def setUp(self):
        configure_admin(self)
        self.state = {}
        self.enterContext(patch.object(admin_auth.st, 'session_state', self.state))
        self.context = self.enterContext(patch.object(admin_auth, 'get_script_run_ctx', return_value=SimpleNamespace(session_id='server-session-one')))

    def test_flags_and_arbitrary_proof_do_not_grant_permission(self):
        self.state.update(is_admin=True, admin_authenticated=True, diagnostic_open=True, admin_proof='forged')
        self.assertFalse(admin_auth.is_admin())
        with self.assertRaises(PermissionError):
            admin_auth.require_admin()

    def test_success_failure_and_session_binding(self):
        self.assertFalse(admin_auth.authenticate('incorrect-test-password'))
        self.assertFalse(admin_auth.is_admin())
        self.assertTrue(admin_auth.authenticate(PASSWORD))
        self.assertTrue(admin_auth.is_admin())
        self.assertNotIn(PASSWORD, str(self.state))
        proof = self.state['admin_proof']
        self.state['admin_proof'] = '\ud800' * 43
        self.assertFalse(admin_auth.is_admin())
        self.state['admin_proof'] = proof
        self.context.return_value = SimpleNamespace(session_id='server-session-two')
        self.assertFalse(admin_auth.is_admin())  # copied proof cannot authenticate another session

    def test_expiry(self):
        self.assertTrue(admin_auth.authenticate(PASSWORD))
        with patch.object(admin_auth.time, 'monotonic', return_value=admin_auth.time.monotonic()+admin_auth.TTL+1):
            self.assertFalse(admin_auth.is_admin())

    def test_logout_revokes_even_if_old_proof_is_restored(self):
        admin_auth.authenticate(PASSWORD)
        proof = self.state['admin_proof']
        admin_auth.logout()
        self.state['admin_proof'] = proof
        self.assertFalse(admin_auth.is_admin())
        self.assertFalse(self.state['diagnostic_open'])

    def test_configuration_missing_malformed_or_rotated_fails_closed(self):
        admin_auth.authenticate(PASSWORD)
        with patch.dict(os.environ, {admin_auth.HASH_NAME: ''}):
            self.assertFalse(admin_auth.configured())
            self.assertFalse(admin_auth.is_admin())
            self.assertFalse(admin_auth.authenticate(PASSWORD))
        self.assertFalse(admin_auth.is_admin())
        for raw in ['not-a-hash', 'pbkdf2_sha256$1$aa$bb', 'pbkdf2_sha256$999999999999$aa$bb']:
            with patch.dict(os.environ, {admin_auth.HASH_NAME:raw}):
                self.assertFalse(admin_auth.configured())
        admin_auth.authenticate(PASSWORD)
        with patch.dict(os.environ, {admin_auth.HASH_NAME:admin_auth.hash_password('another-test-only-admin-password')}):
            self.assertFalse(admin_auth.is_admin())

    def test_throttle_applies_across_sessions_and_resets_after_window(self):
        for index in range(admin_auth.ATTEMPTS):
            self.context.return_value = SimpleNamespace(session_id=f'server-session-{index}')
            self.assertFalse(admin_auth.authenticate('wrong-test-password'))
        self.assertFalse(admin_auth.authenticate(PASSWORD))
        with patch.object(admin_auth.time, 'monotonic', return_value=admin_auth.time.monotonic()+admin_auth.WINDOW+1):
            self.assertTrue(admin_auth.authenticate(PASSWORD))

    def test_direct_diagnostic_call_denied_before_store_or_api(self):
        with patch('dart_insight.services.runtime.open_store') as store, patch('dart_insight.opendart.client.DartClient') as client:
            with self.assertRaises(PermissionError):
                render_diagnostics(Settings())
            store.assert_not_called()
            client.assert_not_called()
        self.context.return_value = None
        self.assertFalse(admin_auth.authenticate(PASSWORD))
        with self.assertRaises(PermissionError):
            render_diagnostics(Settings())

    def test_streamlit_secret_supported_and_empty_environment_does_not_fallback(self):
        value = os.environ[admin_auth.HASH_NAME]
        with patch.dict(os.environ, {}, clear=True), patch.object(admin_auth.st, 'secrets', {admin_auth.HASH_NAME:value}):
            self.assertTrue(admin_auth.authenticate(PASSWORD))
        with patch.dict(os.environ, {admin_auth.HASH_NAME:''}), patch.object(admin_auth.st, 'secrets', {admin_auth.HASH_NAME:value}):
            self.assertFalse(admin_auth.configured())


class AdminUiTests(unittest.TestCase):
    setUp = fixture.RagUiTests.setUp
    answer = fixture.RagUiTests.answer

    def test_public_menu_and_forged_url_state_cannot_execute_diagnostics(self):
        app = AppTest.from_file(fixture.APP).run()
        self.assertEqual(len(app.toggle),0)
        with patch('dart_insight.ui.diagnostics.render_diagnostics') as diagnostics:
            app.query_params.update(admin='diagnostics', is_admin='true')
            app.session_state['diagnostic_open']=True
            app.session_state['is_admin']=True
            app.session_state['admin_proof']='forged'
            app.run()
            diagnostics.assert_not_called()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.toggle),0)

    def test_login_failure_success_clearing_password_and_logout(self):
        app = AppTest.from_file(fixture.APP).run()
        app.query_params['admin']='login'
        app.run()
        app.text_input(key='_admin_password').set_value('wrong-test-admin-password')
        app.button(key='FormSubmitter:admin_login-로그인').click().run()
        self.assertTrue(any('로그인하지 못했습니다' in e.value for e in app.error))
        self.assertEqual(app.session_state['_admin_password'],'')
        login(app)
        self.assertEqual(len(app.toggle),1)
        proof=app.session_state['admin_proof']
        app.toggle(key='diagnostic_open').set_value(True).run()
        self.assertTrue(any(h.value=='진단 도구' for h in app.title))
        app.button(key='admin_logout').click().run()
        self.assertEqual(len(app.toggle),0)
        self.assertFalse(app.session_state['diagnostic_open'])
        app.session_state['admin_proof']=proof
        app.session_state['diagnostic_open']=True
        with patch('dart_insight.ui.diagnostics.render_diagnostics') as diagnostics:
            app.run()
            diagnostics.assert_not_called()
        self.service.ask.assert_not_called()

    def test_missing_configuration_has_no_login_or_diagnostic_action(self):
        with patch.dict(os.environ, {admin_auth.HASH_NAME:''}):
            app=AppTest.from_file(fixture.APP).run()
            app.query_params['admin']='login';app.run()
            self.assertEqual(len(app.toggle),0)
            self.assertEqual(len(app.text_input),0)
            self.assertTrue(any('管理' in i.value or '사용할 수 없습니다' in i.value for i in app.info))
