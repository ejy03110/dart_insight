"""All financial statements here are synthetic, not facts about a particular filing."""
import json
import unittest
from unittest.mock import Mock, patch

from dart_insight.config import Settings
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.pipeline import UNKNOWN, build_graph, validate_answer
from dart_insight.rag.retrieval import Evidence
from dart_insight.rag.validation import AnswerValidationError


def doc(text, identifier='doc:synthetic', kind='opendart_document'):
    return Evidence(identifier, text, '합성 공시', 'https://dart.fss.or.kr', kind)


class CoreClaimTests(unittest.TestCase):
    def validate(self, answer, *sources):
        return validate_answer({'answer': answer, 'supported': True}, list(sources), '공시의 영향은?')

    def rejected(self, answer, sources, rule):
        with self.assertRaises(AnswerValidationError) as caught:
            self.validate(answer, *sources)
        self.assertEqual((caught.exception.category, caught.exception.rule), ('grounding', rule))

    def test_third_party_does_not_prove_existing_shareholder_subscription_or_allocation(self):
        for answer in ('기존 주주에게 신주 청약 기회가 있습니다. [근거 1]',
                       '해석하면 기존 주주도 청약에 참여할 수 있습니다. [근거 1]',
                       '기존 주주에게 신주가 배정됩니다. [근거 1]',
                       '배정 대상자는 기존 주주입니다. [근거 1]'):
            with self.subTest(answer=answer):
                self.rejected(answer, [doc('증자방식: 제3자배정증자. 배정대상자: 외부 투자자 A사.')],
                              'shareholder_allocation_not_supported')

    def test_named_existing_shareholder_is_not_a_general_subscription_right(self):
        self.rejected('기존 주주에게 청약 기회가 있습니다. [근거 1]',
                      [doc('제3자배정증자. 배정대상자: A사(기존 주주).')], 'shareholder_allocation_not_supported')
        self.validate('기존 주주에게 신주 청약 기회가 있습니다. [근거 1]',
                      doc('주주배정 방식으로 기존 주주에게 신주 청약 기회가 부여됩니다.'))

    def test_new_share_lockup_cannot_restrict_old_holders(self):
        for answer in ('기존 주주는 1년간 주식을 팔 수 없습니다. [근거 1]',
                       '기존 주주에게도 의무보유 조건이 적용됩니다. [근거 1]',
                       '기존 주식은 보호예수 대상입니다. [근거 1]'):
            self.rejected(answer, [doc('신주 인수자는 배정 신주 전량을 1년간 의무보유합니다.')],
                          'existing_holder_restriction_not_supported')

    def test_direct_old_holder_condition_and_indirect_interpretation_remain_valid(self):
        self.validate('기존 주주에게 매도 제한이 적용됩니다. [근거 1]',
                      doc('기존 주주에게 계약상 매도 제한이 적용됩니다.'))
        source = doc('신주 인수자가 배정 신주를 1년간 의무보유합니다.')
        for answer in ('공시 사실: 신주 인수자의 의무보유입니다. [근거 1]\n'
                       '해석: 기존 주주의 단기 수급 부담이 줄어들 수 있으나 주가 효과는 확인되지 않습니다. [근거 1]',
                       '기존 주주는 신주 인수자의 의무보유로 단기 유통물량에 영향을 받을 수 있습니다. [근거 1]',
                       '기존 주주는 신주의 보호예수로 영향을 받을 수 있습니다. [근거 1]'):
            self.validate(answer, source)

    def test_purpose_absence_zero_dash_and_negative_sentence_are_not_support(self):
        for body in ('자금조달의 목적: 운영자금. 채무상환자금: 없음.',
                     '운영자금(원)\n100,000\n채무상환자금(원)\n0',
                     '채무상환자금: -', '채무상환자금: 0.0원',
                     '조달자금은 채무상환에 사용하지 않습니다.'):
            self.rejected('조달 자금은 채무상환에 사용합니다. [근거 1]', [doc(body)], 'funding_purpose_not_supported')
        self.rejected('해석하면 조달 자금은 시설자금으로 사용될 수 있습니다. [근거 1]',
                      [doc('자금조달의 목적은 운영자금입니다.')], 'funding_purpose_not_supported')

    def test_positive_purpose_amount_and_explicit_use_pass(self):
        for body in ('운영자금(원): 120,000', '운영자금(원)\n120,000',
                     '자금조달의 목적은 운영자금입니다.', '조달자금을 운영자금으로 사용합니다.'):
            result = self.validate('조달 자금은 운영자금으로 사용됩니다. [근거 1]', doc(body))
            self.assertEqual(result['citations'][0]['id'], 'doc:synthetic')
        self.validate('조달 자금은 운영자금과 채무상환에 사용됩니다. [근거 1]',
                      doc('운영자금(원): 100,000\n채무상환자금(원): 20,000'))

    def test_local_citation_must_support_claim_not_some_uncited_chunk(self):
        sources = [doc('자금조달의 목적은 운영자금입니다.', 'doc:one'),
                   doc('기존 주주에게 신주 청약 기회가 있습니다.', 'doc:two')]
        self.rejected('기존 주주에게 신주 청약 기회가 있습니다. [근거 1]', sources,
                      'shareholder_allocation_not_supported')
        self.validate('기존 주주에게 신주 청약 기회가 있습니다. [근거 2]', *sources)
        self.rejected('조달자금은 운영자금으로 사용됩니다. [근거 2]', sources, 'funding_purpose_not_supported')

    def test_metadata_and_uncited_tail_cannot_supply_financial_support(self):
        self.rejected('조달 자금은 운영자금으로 사용합니다. [근거 1]',
                      [doc('자금조달의 목적은 운영자금입니다.', kind='opendart_metadata')], 'funding_purpose_not_supported')
        self.rejected('공시가 제출됐습니다. [근거 1]\n조달 자금은 시설자금으로 사용합니다.',
                      [doc('시설자금(원): 100,000')], 'funding_purpose_not_supported')

    def test_negative_acknowledgement_and_false_schema_do_not_create_new_facts(self):
        self.validate('기존 주주의 청약 권리는 제공된 근거에서 확인되지 않습니다. [근거 1]', doc('제3자배정증자.'))
        self.validate('조달 자금의 채무상환 목적은 확인되지 않습니다. [근거 1]', doc('운영자금(원): 100,000'))
        self.validate('기존 주주의 의무보유 조건은 명시되지 않았습니다. [근거 1]', doc('신주 인수자의 의무보유 조건.'))
        self.assertEqual(validate_answer({'answer': 'unknown', 'supported': False}, [], '질문'),
                         {'answer': UNKNOWN, 'citations': []})

    def test_conflicting_purpose_excerpts_fail_closed(self):
        self.rejected('조달 자금은 채무상환에 사용됩니다. [근거 1] [근거 2]',
                      [doc('채무상환자금: 0'), doc('채무상환자금: 100')], 'funding_purpose_not_supported')

    @patch('time.sleep')
    def test_gms_corrects_bad_claim_or_reports_insufficient_support(self, sleep):
        session = Mock()
        def response(answer, supported=True):
            return Mock(status_code=200, json=lambda: {'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps({'answer': answer, 'supported': supported})}}]})
        session.post.side_effect = [response('기존 주주에게 청약 기회가 있습니다. [근거 1]'),
                                    response('근거 부족', False)]
        llm = GmsClient(Settings(openai_api_key='test-only', request_interval_seconds=0), session)
        result = build_graph(lambda _: [doc('제3자배정증자.')], llm).invoke({'question': '기존 주주 영향은?'})
        self.assertEqual(result['answer'], UNKNOWN)
        self.assertEqual(result['citations'], [])
        self.assertEqual(session.post.call_count, 2)
        session.post.side_effect = None
        session.post.return_value = response('기존 주주에게 청약 기회가 있습니다. [근거 1]')
        session.post.reset_mock()
        with self.assertRaises(LLMError) as caught:
            build_graph(lambda _: [doc('제3자배정증자.')], llm).invoke({'question': '영향은?'})
        self.assertIn('근거가 부족', str(caught.exception))
        self.assertEqual(session.post.call_count, 3)
