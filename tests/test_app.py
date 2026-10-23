"""Streamlit in-process UI checks; not a browser screenshot or live API test."""

from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from admin_fixture import configure_admin, login

from dart_insight.config import Settings
from dart_insight.opendart.client import DartError

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class AppTests(unittest.TestCase):
    def setUp(self):
        configure_admin(self)
        from dart_insight.services.recent_disclosures import RecentDisclosureService
        self.enterContext(patch('dart_insight.ui.recent.recent_service', return_value=RecentDisclosureService(Settings())))

    def test_missing_key_and_disclaimer(self):
        with patch("dart_insight.config.load_settings", return_value=Settings()):
            app = AppTest.from_file(APP).run()
            self.assertTrue(any("투자 조언이 아닙니다" in c.value for c in app.caption))
            login(app)
            app.toggle(key='diagnostic_open').set_value(True).run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("DART_API_KEY", app.error[0].value)


    def test_empty_api_result_notice(self):
        with patch("dart_insight.config.load_settings", return_value=Settings(dart_api_key="test")), \
             patch("dart_insight.opendart.client.DartClient") as client:
            api = client.return_value.__enter__.return_value
            api.resolve_company.return_value = {"corp_code": "00126380"}
            api.list_disclosures.return_value = []
            app = AppTest.from_file(APP).run()
            login(app)
            app.toggle(key='diagnostic_open').set_value(True).run()
            app.button(key='connect_dart').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("조회된 공시가 없습니다" in notice.value for notice in app.info))

    def test_connection_failure_shows_safe_error(self):
        with patch("dart_insight.config.load_settings", return_value=Settings(dart_api_key="test")), \
             patch("dart_insight.opendart.client.DartClient") as client:
            client.return_value.__enter__.return_value.resolve_company.side_effect = DartError("연결 실패")
            app = AppTest.from_file(APP).run()
            login(app)
            app.toggle(key='diagnostic_open').set_value(True).run()
            app.button(key='connect_dart').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.error[0].value, "연결 실패")
