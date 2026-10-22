import unittest
from unittest.mock import Mock
from dart_insight.config import Settings, ConfigurationError
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.response import response_value, ResponseError


def response(content, reason='stop', **fields):
    return Mock(status_code=200, json=lambda: {'choices': [dict(
        message={'content': content, **fields}, finish_reason=reason)]})


class StabilityTests(unittest.TestCase):
    def test_lossless_fences_and_text_blocks(self):
        for content in ('```json\n{"answer":"한글"}\n```',
                        [{'type': 'text', 'text': '{"answer":'}, {'type': 'text', 'text': '"한글"}'}]):
            self.assertEqual(response_value(response(content)), {'answer': '한글'})

    def test_truncation_refusal_and_unknown_finish_are_not_accepted(self):
        for reason, category in [('length', 'truncated'), ('content_filter', 'refusal'), ('tool_calls', 'finish_reason')]:
            with self.assertRaises(ResponseError) as caught:
                response_value(response('{"answer":"valid but partial"}', reason))
            self.assertEqual(caught.exception.category, category)

    def test_extra_prose_duplicates_and_nan_rejected(self):
        for content in ('Here is JSON: {}', '{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(ResponseError):
                response_value(response(content))

    def test_terminal_errors_not_retried_and_logs_contain_no_content_or_key(self):
        session = Mock()
        settings = Settings(openai_api_key='NEVER_LOG_KEY', request_interval_seconds=0)
        for item in (response('PRIVATE_BODY', 'length'), response(None, refusal='PRIVATE_BODY'),
                     Mock(status_code=200, json=lambda: {})):
            session.post.return_value = item
            session.post.reset_mock()
            with self.assertLogs('dart_insight.operations', level='INFO') as logs:
                with self.assertRaises(LLMError):
                    GmsClient(settings, session).json('PRIVATE_PROMPT', {}, lambda x: x)
            self.assertEqual(session.post.call_count, 1)
            self.assertNotIn('PRIVATE', ''.join(logs.output))
            self.assertNotIn('NEVER_LOG_KEY', ''.join(logs.output))
            item.close.assert_called_once()

    def test_schema_retry_sends_correction_and_never_raw_output(self):
        session = Mock()
        session.post.side_effect = [response('PRIVATE_BAD_OUTPUT'), response('{"ok":true}')]
        result = GmsClient(Settings(openai_api_key='test', request_interval_seconds=0), session).json(
            'schema', {}, lambda v: v)
        self.assertEqual(result, {'ok': True})
        request = session.post.call_args.kwargs['json']
        self.assertEqual(len(request['messages']), 3)
        self.assertNotIn('PRIVATE_BAD_OUTPUT', str(request))
        self.assertEqual(request['max_completion_tokens'], 4096)

    def test_cpu_batch_and_output_limits(self):
        for values in ({'embedding_batch_size': 0}, {'gms_max_output_tokens': 0}):
            with self.assertRaises(ConfigurationError):
                Settings(**values)
