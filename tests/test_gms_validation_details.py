"""Content-free diagnostics for the shared analysis/chat validator."""
import unittest
from unittest.mock import Mock

from dart_insight.config import Settings
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.pipeline import build_graph, validate_answer
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION, ANSWER_PROMPT
from dart_insight.rag.prompts.selection import selected_ids
from dart_insight.rag.retrieval import Evidence
from dart_insight.rag.validation import AnswerValidationError, SelectionValidationError


def evidence():
    return [Evidence('meta:receipt', '공시 메타데이터', '제목', 'https://dart.fss.or.kr', 'opendart_metadata'),
            Evidence('doc:receipt:hash:1074', '환율 위험', '제목', 'https://dart.fss.or.kr', 'opendart_document')]


class DetailedValidationTests(unittest.TestCase):
    def test_exact_schema_fields_and_types(self):
        cases = [([], '$', 'object_required'), ({}, 'answer', 'required_field'),
                 ({'answer': 'PRIVATE'}, 'supported', 'required_field'),
                 ({'answer': 'PRIVATE', 'supported': True, 'PRIVATE_FIELD': 1}, '$', 'unexpected_fields'),
                 ({'answer': 'PRIVATE', 'supported': 'true'}, 'supported', 'boolean_required'),
                 ({'answer': [], 'supported': True}, 'answer', 'string_required'),
                 ({'answer': ' ', 'supported': True}, 'answer', 'nonempty_string')]
        for value, field, rule in cases:
            with self.subTest(rule=rule), self.assertRaises(AnswerValidationError) as caught:
                validate_answer(value, evidence())
            self.assertEqual((caught.exception.category, caught.exception.field, caught.exception.rule),
                             ('schema', field, rule))

    def test_citation_rules_distinguished_without_accepting_fake_sources(self):
        for answer, rule in [('PRIVATE', 'citation_required'), ('내용 [근거 3]', 'citation_number_out_of_range'),
                             ('내용 [근거 0]', 'citation_number_out_of_range'),
                             ('내용 [근거 1] [근거 1, 2]', 'citation_token_format')]:
            with self.subTest(rule=rule), self.assertRaises(AnswerValidationError) as caught:
                validate_answer({'answer': answer, 'supported': True}, evidence())
            self.assertEqual(caught.exception.category, 'citation')
            self.assertEqual(caught.exception.rule, rule)

    def test_payload_number_maps_to_exact_real_chunk_id_for_analysis_and_chat(self):
        for question in (ANALYSIS_QUESTION, '위험 요인은?'):
            llm = Mock()
            def generate(system, payload, validate):
                self.assertEqual(system, ANSWER_PROMPT)
                self.assertEqual([(e['number'], e['id']) for e in payload['evidence']],
                                 [(1, 'meta:receipt'), (2, 'doc:receipt:hash:1074')])
                return validate({'answer': '환율 위험 [근거 2]', 'supported': True})
            llm.json.side_effect = generate
            result = build_graph(lambda _: evidence(), llm).invoke({'question': question})
            self.assertEqual(result['citations'][0]['id'], 'doc:receipt:hash:1074')
            self.assertEqual(result['citations'][0]['number'], 2)

    def test_logs_static_class_field_rule_and_no_provider_names_or_values(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200, json=lambda: {'choices': [{
            'finish_reason': 'stop', 'message': {'content':
                '{"answer":"PRIVATE_RESPONSE", "supported":"true"}'}}]})
        with self.assertLogs('dart_insight.operations', level='INFO') as logs:
            with self.assertRaises(LLMError):
                GmsClient(Settings(openai_api_key='PRIVATE_KEY', request_interval_seconds=0), session).json(
                    ANSWER_PROMPT, {'evidence': [e.to_dict() for e in evidence()]},
                    lambda v: validate_answer(v, evidence()))
        output = '\n'.join(logs.output)
        for item in ('exception=AnswerValidationError', 'category=schema', 'field=supported',
                     'rule=boolean_required', 'evidence_count=2'):
            self.assertIn(item, output)
        self.assertNotIn('PRIVATE', output)
        self.assertNotIn('doc:receipt', output)
        self.assertEqual(session.post.call_count, 3)

    def test_unknown_validator_bug_not_retried_or_logged_raw(self):
        session = Mock()
        session.post.return_value = Mock(status_code=200, json=lambda: {'choices': [{
            'message': {'content': '{}'}}]})
        def broken(_):
            raise KeyError('PRIVATE_KEY_OR_FIELD')
        with self.assertLogs('dart_insight.operations', level='INFO') as logs:
            with self.assertRaises(LLMError):
                GmsClient(Settings(openai_api_key='test', request_interval_seconds=0), session).json('x', {}, broken)
        self.assertIn('exception=KeyError', '\n'.join(logs.output))
        self.assertNotIn('PRIVATE', '\n'.join(logs.output))
        self.assertEqual(session.post.call_count, 1)

    def test_selection_field_type_and_actual_allowed_id(self):
        for value, rule in [({'selected_ids': 'PRIVATE'}, 'array_required'),
                            ({'selected_ids': [3]}, 'string_items_required'),
                            ({'selected_ids': ['invented']}, 'candidate_id_not_allowed')]:
            with self.subTest(rule=rule), self.assertRaises(SelectionValidationError) as caught:
                selected_ids(value, {'real'})
            self.assertEqual(caught.exception.rule, rule)
        self.assertEqual(selected_ids({'selected_ids': ['real']}, {'real'}), ['real'])

    def test_grounding_rules_remain_strict(self):
        for text, rule in [('취득 목적: 장내매수 [근거 2]', 'acquisition_method_is_not_purpose'),
                           ('취득 270주 [근거 2]', 'missing_purpose_acknowledgement')]:
            with self.subTest(rule=rule), self.assertRaises(AnswerValidationError) as caught:
                validate_answer({'answer': text, 'supported': True}, evidence(), '취득 목적은?')
            self.assertEqual(caught.exception.category, 'grounding')
            self.assertEqual(caught.exception.rule, rule)
