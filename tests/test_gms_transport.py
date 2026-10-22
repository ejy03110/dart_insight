import unittest
from unittest.mock import Mock, patch
import requests

from dart_insight.config import Settings
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.query_extraction import extract_queries, QueryExtractionError
from dart_insight.data.models import Disclosure


def response(content, status=200):
    return Mock(status_code=status, json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})


@patch('time.sleep')
class UnifiedTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(openai_api_key='PRIVATE_KEY', request_interval_seconds=0)
        self.selected = Disclosure('00126380', '삼성전자', '005930', '사업보고서', '20250311001085',
            '2025-03-11', '정기공시', 'https://dart.fss.or.kr', '')

    def call(self, kind, session):
        if kind == 'query':
            return extract_queries(self.selected, '위험?', self.settings, session=session)
        return GmsClient(self.settings, session).json('schema', {'evidence': []}, lambda v: v)

    def test_terminal_statuses_once_and_safe_diagnostics(self, sleep):
        for kind in ('query', 'answer'):
            for status, category in ((401, 'authentication'), (403, 'authentication'),
                                     (404, 'not_found'), (400, 'request'), (302, 'request')):
                with self.subTest(kind=kind, status=status):
                    session = Mock(); item = response('PRIVATE_BODY', status)
                    session.post.return_value = item
                    with self.assertLogs('dart_insight.operations', level='INFO') as logs:
                        with self.assertRaises((QueryExtractionError, LLMError)) as caught:
                            self.call(kind, session)
                    self.assertIn(category, str(caught.exception))
                    self.assertIn(str(status), str(caught.exception))
                    self.assertNotIn('PRIVATE', ''.join(logs.output) + str(caught.exception))
                    self.assertEqual(session.post.call_count, 1)
                    item.close.assert_called_once()

    def test_transient_status_recovery_and_bounded_backoff_both_paths(self, sleep):
        for kind in ('query', 'answer'):
            for status in (429, 500, 501, 502, 503, 504, 599):
                with self.subTest(kind=kind, status=status):
                    session = Mock(); bad = response('', status)
                    good = response('{"queries":["위험"]}' if kind == 'query' else '{"ok":true}')
                    session.post.side_effect = [bad, good]
                    self.call(kind, session)
                    self.assertEqual(session.post.call_count, 2)
                    bad.close.assert_called_once(); good.close.assert_called_once()
                    session.post.side_effect = None; session.post.return_value = response('', status)
                    session.post.reset_mock(); sleep.reset_mock()
                    with self.assertRaises((QueryExtractionError, LLMError)):
                        self.call(kind, session)
                    self.assertEqual(session.post.call_count, 3)
                    self.assertEqual([c.args[0] for c in sleep.call_args_list if c.args[0] >= 1], [1, 2])

    def test_timeout_network_recovery_and_exhaustion(self, sleep):
        for kind in ('query', 'answer'):
            for error, category in ((requests.Timeout('PRIVATE_URL'), 'timeout'),
                                    (requests.ConnectionError('PRIVATE_KEY'), 'network')):
                session = Mock()
                good = response('{"queries":["위험"]}' if kind == 'query' else '{}')
                session.post.side_effect = [error, good]
                self.call(kind, session)
                self.assertEqual(session.post.call_count, 2)
                session.post.side_effect = error; session.post.reset_mock()
                with self.assertLogs('dart_insight.operations', level='INFO') as logs:
                    with self.assertRaises((QueryExtractionError, LLMError)) as caught:
                        self.call(kind, session)
                self.assertEqual(session.post.call_count, 3)
                self.assertIn(category, str(caught.exception))
                self.assertNotIn('PRIVATE', str(caught.exception) + ''.join(logs.output))

    def test_auth_extraction_failure_prevents_repeated_auth_calls(self, sleep):
        session = Mock(); session.post.return_value = response('', 401)
        client = GmsClient(self.settings, session)
        with self.assertRaises(QueryExtractionError):
            client.extract(self.selected, '위험?')
        for payload in ({'candidates': []}, {'evidence': []}):
            with self.assertRaises(LLMError) as caught:
                client.json('schema', payload, lambda v: v)
            self.assertIn('authentication', str(caught.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_transport_and_json_failures_share_one_budget(self, sleep):
        for kind in ('query', 'answer'):
            session = Mock()
            session.post.side_effect = [response('', 503), response('PRIVATE_INVALID_JSON'), response('', 429)]
            with self.assertRaises((QueryExtractionError, LLMError)):
                self.call(kind, session)
            self.assertEqual(session.post.call_count, 3)

    def test_successful_extraction_clears_previous_terminal_error(self, sleep):
        session = Mock()
        client = GmsClient(self.settings, session)
        session.post.return_value = response('', 401)
        with self.assertRaises(QueryExtractionError):
            client.extract(self.selected, '위험?')
        session.post.return_value = response('{"queries":["위험"]}')
        self.assertIn('위험', client.extract(self.selected, '위험?'))
        session.post.return_value = response('{"ok":true}')
        self.assertEqual(client.json('schema', {'evidence': []}, lambda v: v), {'ok': True})

    def test_only_allowlisted_provider_codes_and_parameter_names_are_exposed(self, sleep):
        for details, expected in [({'code': 'unsupported_parameter', 'param': 'max_completion_tokens',
                                    'message': 'PRIVATE_BODY'}, 'unsupported_parameter'),
                                  ({'code': 'PRIVATE_CODE', 'param': 'PRIVATE_FIELD',
                                    'message': 'PRIVATE_BODY'}, 'category=request')]:
            session = Mock()
            session.post.return_value = Mock(status_code=400, json=lambda: {'error': details})
            with self.assertLogs('dart_insight.operations', level='INFO') as logs:
                with self.assertRaises(QueryExtractionError) as caught:
                    self.call('query', session)
            self.assertIn(expected, '\n'.join(logs.output))
            self.assertNotIn('PRIVATE', '\n'.join(logs.output) + str(caught.exception))
