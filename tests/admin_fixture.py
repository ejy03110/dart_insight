"""Synthetic credentials exclusively for local tests; no production secret."""
import os
from unittest.mock import patch
from dart_insight.ui import admin_auth

PASSWORD = 'test-only-admin-password-for-ui'
PASSWORD_HASH = admin_auth.hash_password(PASSWORD)


def configure_admin(test):
    test.enterContext(patch.dict(os.environ, {admin_auth.HASH_NAME: PASSWORD_HASH}))
    with admin_auth._lock:
        admin_auth._grants.clear()
        admin_auth._failures.clear()


def login(app):
    app.query_params['admin'] = 'login'
    app.run()
    app.text_input(key='_admin_password').set_value(PASSWORD)
    app.button(key='FormSubmitter:admin_login-로그인').click().run()
    app.query_params.clear()
    app.run()
    return app
