"""Synthetic fixtures solely for isolated tests; never production fallback data."""

from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zipfile import ZipFile

import requests

from dart_insight.config import ConfigurationError, Settings
from dart_insight.data.models import COMPANIES
from dart_insight.opendart.client import DartClient, DartError


def response(payload=None, content=b""):
    result = Mock(status_code=200, content=content)
    result.json.return_value = payload
    return result


def row(receipt="20250101000001", corp_code="00126380"):
    return {"corp_code": corp_code, "corp_name": "테스트기업", "stock_code": "005930",
            "report_nm": "테스트 보고서", "rcept_no": receipt, "rcept_dt": "20250101"}


def page(rows, total=1):
    return response({"status": "000", "total_page": total, "list": rows})


def corp_zip():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", "<result><list><corp_code>00126380</corp_code>"
                         "<corp_name>삼성전자</corp_name><stock_code>005930</stock_code>"
                         "</list></result>")
    return buffer.getvalue()


class DartClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = Mock()
        self.settings = Settings(dart_api_key="test-secret-never-print", data_dir=Path(self.temp.name),
                                 request_interval_seconds=0)
        self.client = DartClient(self.settings, session=self.session)
        self.addCleanup(self.client.close)

    def fetch(self, **kwargs):
        return self.client.list_disclosures("00126380", date(2025, 1, 1), date(2025, 12, 31),
                                            disclosure_type="A", **kwargs)

    def test_missing_key_prevents_network(self):
        with self.assertRaises(ConfigurationError):
            DartClient(replace(self.settings, dart_api_key=""), session=self.session)
        self.session.get.assert_not_called()

    def test_corporate_zip_and_stock_code_resolution_cached(self):
        self.session.get.return_value = response(content=corp_zip())
        self.assertEqual(self.client.resolve_company(COMPANIES[0])["corp_code"], "00126380")
        self.client.resolve_company(COMPANIES[0])
        self.session.get.assert_called_once()

    def test_company_not_found_is_error(self):
        self.session.get.return_value = response(content=corp_zip())
        with self.assertRaises(DartError):
            self.client.resolve_company(COMPANIES[2])

    def test_corporate_xml_api_error_not_zip(self):
        self.session.get.return_value = response(content=b"<result><status>010</status></result>")
        with self.assertRaises(DartError) as caught:
            self.client.corporate_codes()
        self.assertEqual(caught.exception.code, "010")

    def test_invalid_zip_and_entity_payload_rejected(self):
        self.session.get.return_value = response(content=b"PKbroken")
        with self.assertRaises(DartError):
            self.client.corporate_codes()
        self.session.get.return_value = response(content=b'<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>')
        with self.assertRaises(DartError):
            self.client.corporate_codes()

    def test_pagination_dedup_normalization_and_source(self):
        self.session.get.side_effect = [page([row()], 2), page([row(), row("20250101000002")], 2)]
        items = self.fetch()
        self.assertEqual(len(items), 2)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(items[0].rcept_dt, "2025-01-01")
        self.assertEqual(items[0].disclosure_type, "정기공시")
        self.assertIn("rcpNo=20250101000001", items[0].source_url)
        self.assertNotIn(self.settings.dart_api_key, items[0].to_index_text())
        for text in ("기업명", "공시명", "접수번호", "접수일", "공시 유형", "원문 URL"):
            self.assertIn(text, items[0].to_index_text())
        request = self.session.get.call_args
        self.assertEqual(request.kwargs["params"]["page_no"], 2)
        self.assertEqual(request.kwargs["timeout"], 20)
        self.assertFalse(request.kwargs["allow_redirects"])
        self.assertNotIn("verify", request.kwargs)  # requests TLS verification default retained

    def test_page_limit_and_success_cache_refresh(self):
        self.session.get.return_value = page([row()], 2)
        self.fetch(max_pages=1)
        self.fetch(max_pages=1)
        self.assertEqual(self.session.get.call_count, 1)
        self.fetch(max_pages=1, refresh=True)
        self.assertEqual(self.session.get.call_count, 2)
        for file in Path(self.temp.name).rglob("*.json"):
            self.assertNotIn(self.settings.dart_api_key, file.read_text())
            self.assertNotIn("crtfc_key", file.read_text())

    def test_empty_result_is_not_error(self):
        self.session.get.return_value = response({"status": "013"})
        self.assertEqual(self.fetch(), [])

    def test_auth_quota_and_server_errors_not_cached(self):
        for status in ("010", "020", "800", "900"):
            with self.subTest(status=status):
                self.session.get.return_value = response({"status": status, "message": "untrusted secret"})
                with self.assertRaises(DartError) as caught:
                    self.fetch()
                self.assertEqual(caught.exception.code, status)
                self.assertNotIn("untrusted secret", str(caught.exception))
        self.assertEqual(list(Path(self.temp.name).rglob("*.json")), [])

    def test_transport_error_hides_key_and_request_url(self):
        for error in (requests.Timeout(self.settings.dart_api_key),
                      requests.ConnectionError(f"url?crtfc_key={self.settings.dart_api_key}")):
            with self.subTest(error=type(error).__name__):
                self.session.get.side_effect = error
                with self.assertRaises(DartError) as caught:
                    self.fetch()
                self.assertNotIn(self.settings.dart_api_key, str(caught.exception))

    def test_malformed_json_and_fields_rejected(self):
        malformed = response()
        malformed.json.side_effect = ValueError("not json")
        for result in (malformed, response([]), page([{}]), page([row(corp_code="12345678")]),
                       page([row(receipt="invalid")])):
            with self.subTest(result=result):
                self.session.get.return_value = result
                with self.assertRaises(DartError):
                    self.fetch()

    def test_invalid_query_prevents_network(self):
        with self.assertRaises(ConfigurationError):
            self.fetch(max_pages=0)
        with self.assertRaises(ConfigurationError):
            self.client.list_disclosures("00126380", date(2026, 1, 1), date(2025, 1, 1),
                                        disclosure_type="A")
        self.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
