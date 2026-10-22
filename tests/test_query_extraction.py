"""Offline F2003 checks: no real GMS calls or model downloads."""

import json
import unittest
from unittest.mock import Mock, patch

import requests

from dart_insight.config import ConfigurationError, Settings
from dart_insight.data.models import Disclosure
from dart_insight.rag.query_extraction import (
    GMS_CHAT_URL, QueryExtractionError, extract_queries, parse_queries,
)


class QueryExtractionTests(unittest.TestCase):
    def setUp(self):
        self.disclosure = Disclosure("00126380", "삼성전자", "005930", "사업보고서",
                                     "20250101000001", "2025-01-01", "정기공시",
                                     "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000001",
                                     "2026-01-01T00:00:00Z")
        self.settings = Settings(openai_api_key="test-gms-key", chat_model="configured-model",
                                 request_interval_seconds=0)
        self.session = Mock()

    def response(self, content):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": content}}]}
        return response

    def extract(self, question="사업 위험은 무엇인가?"):
        return extract_queries(self.disclosure, question, self.settings, session=self.session)

    def test_gms_request_and_receipt_added_without_llm_help(self):
        self.session.post.return_value = self.response('{"queries":[" 삼성전자 사업 위험 "]}')
        self.assertEqual(self.extract(), {"queries": ["삼성전자 사업 위험", self.disclosure.rcept_no]})
        args, kwargs = self.session.post.call_args
        self.assertEqual(args, (GMS_CHAT_URL,))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-gms-key"})
        self.assertEqual(kwargs["json"]["model"], "configured-model")
        self.assertEqual(kwargs["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["timeout"], self.settings.timeout_seconds)
        self.assertFalse(kwargs["allow_redirects"])
        messages = kwargs["json"]["messages"]
        self.assertIn("공시 분석 전문가", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["disclosure"], self.disclosure.to_dict())
        self.assertEqual(payload["question"], "사업 위험은 무엇인가?")
        self.session.close.assert_not_called()

    def test_strict_schema_rejects_invalid_outputs(self):
        for content in ('broken', '[]', '{}', '{"queries":[]}', '{"queries":"x"}',
                        '{"queries":[1]}', '{"queries":[null]}', '{"queries":[" "]}',
                        '{"queries":["x"],"extra":1}', '```json\n{"queries":["x"]}\n```', None):
            with self.subTest(content=content), self.assertRaises(QueryExtractionError):
                parse_queries(content)

    def test_normalization_and_receipt_deduplication(self):
        receipt = self.disclosure.rcept_no
        self.session.post.return_value = self.response(json.dumps({"queries": [receipt, "위험", " 위험 "]}))
        self.assertEqual(self.extract(), {"queries": [receipt, "위험"]})

    @patch("dart_insight.rag.query_extraction.time.sleep")
    def test_two_parse_retries_then_success(self, sleep):
        self.session.post.side_effect = [self.response("bad"), self.response('{"queries":[2]}'),
                                         self.response('{"queries":["자금조달 조건"]}')]
        self.assertIn(self.disclosure.rcept_no, self.extract()["queries"])
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertNotIn("bad", str(self.session.post.call_args.kwargs["json"]))

    def test_retry_exhaustion_is_safe_and_bounded(self):
        self.session.post.return_value = self.response("sensitive provider output test-gms-key")
        with self.assertRaises(QueryExtractionError) as caught:
            self.extract()
        self.assertEqual(self.session.post.call_count, 3)
        self.assertIn("2회", str(caught.exception))
        self.assertNotIn("test-gms-key", str(caught.exception))

    def test_malformed_envelope_and_non_json_response_fail_without_retry(self):
        invalid_json = Mock(status_code=200)
        invalid_json.json.side_effect = ValueError("sensitive")
        for response in (invalid_json, Mock(status_code=200, json=lambda: {"choices": []})):
            self.session.post.reset_mock()
            self.session.post.return_value = response
            with self.assertRaises(QueryExtractionError) as caught:
                self.extract()
            self.assertIn('envelope', str(caught.exception))
            self.assertEqual(self.session.post.call_count, 1)
            response.close.assert_called_once()

    @patch("dart_insight.rag.query_extraction.time.sleep")
    def test_http_and_transport_errors_follow_transient_retry_policy(self, sleep):
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status):
                self.session.reset_mock()
                self.session.post.return_value = Mock(status_code=status)
                with self.assertRaises(QueryExtractionError):
                    self.extract()
                self.assertEqual(self.session.post.call_count, 3 if status in (429, 500) else 1)
        self.session.post.side_effect = requests.Timeout("test-gms-key secret response")
        with self.assertRaises(QueryExtractionError) as caught:
            self.extract()
        self.assertNotIn("test-gms-key", str(caught.exception))

    def test_invalid_inputs_and_missing_key_fail_before_network(self):
        for question in ("", " ", None):
            with self.assertRaises(QueryExtractionError):
                self.extract(question)
        with self.assertRaises(QueryExtractionError):
            extract_queries(None, "질문", self.settings, session=self.session)
        with self.assertRaises(ConfigurationError):
            extract_queries(self.disclosure, "질문", Settings(), session=self.session)
        self.session.post.assert_not_called()

    @patch("dart_insight.rag.query_extraction.requests.Session")
    def test_owned_session_closed(self, factory):
        factory.return_value.post.return_value = self.response('{"queries":["위험"]}')
        extract_queries(self.disclosure, "질문", self.settings)
        factory.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
