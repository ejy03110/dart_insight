import json
import unittest
from unittest.mock import Mock

from dart_insight.config import Settings
from dart_insight.rag.conversation import (MAX_CHARACTERS, context_id, contextual_search,
    recent_history, recall_answer, response_style)
from dart_insight.rag.llm import GmsClient
from dart_insight.rag.pipeline import build_graph, UNKNOWN
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION, ANSWER_PROMPT
from dart_insight.rag.retrieval import Evidence
from dart_insight.rag.validation import AnswerValidationError
from dart_insight.services.rag_service import RagService

RECEIPT = '20251203800556'


def turns(answer, question='이번 유상증자의 영향은?'):
    return [{'role': 'user', 'content': question, 'rcept_no': RECEIPT},
            {'role': 'assistant', 'content': answer, 'rcept_no': RECEIPT}]


class ConversationTests(unittest.TestCase):
    def test_context_contains_latest_six_complete_pairs_in_original_order(self):
        history = []
        for n in range(10):
            history += turns(f'답변 {n}', f'질문 {n}')
        recent = recent_history(history, RECEIPT)
        self.assertEqual(len(recent), 12)
        self.assertEqual(recent[0]['content'], '질문 4')
        self.assertEqual(recent[-1]['content'], '답변 9')
        self.assertEqual([t['role'] for t in recent], ['user', 'assistant'] * 6)
        self.assertNotIn('audit', str(recent))

    def test_history_budget_never_truncates_an_answer_or_reuses_older_suffix(self):
        recent = recent_history(turns('이전') + turns('a' * (MAX_CHARACTERS + 1)), RECEIPT)
        self.assertEqual(recent, [])
        history = recent_history(turns('가' * 7000) + turns('나' * 7000), RECEIPT)
        self.assertEqual(len(history), 2)
        self.assertTrue(history[-1]['content'].endswith('나' * 50))
        self.assertLessEqual(sum(len(t['content']) for t in history), MAX_CHARACTERS)

    def test_foreign_malformed_and_incomplete_history_are_not_recalled(self):
        foreign = turns('다른 공시 내용')
        for item in foreign:
            item['rcept_no'] = '20250311001085'
        for history in (foreign, turns('같은 공시') + foreign, [None, None],
                        turns('같은 공시') + [{'role': 'user', 'content': '미완성'}]):
            self.assertEqual(recent_history(history, RECEIPT), [])

    def test_exact_third_reason_comes_from_immediately_previous_answer(self):
        history = turns('1. 첫 답변\n2. 과거 내용\n3. 오래된 이유') + turns(
            '1. 지분이 희석될 수 있습니다. [근거 2]\n2. 발행가액을 확인해야 합니다. [근거 3]\n'
            '3. RPS 발행으로 기존 주주의 의결권과 배당 권한이 약화될 수 있습니다. [근거 4]')
        result = recall_answer('방금 네가 설명한 내용 중 세 번째 이유가 뭐였지? 한 문장으로만 답해줘.',
                               recent_history(history, RECEIPT))
        self.assertIn('RPS 발행으로 기존 주주의 의결권과 배당 권한이 약화될 수 있습니다.', result['answer'])
        self.assertNotIn('오래된 이유', result['answer'])
        self.assertNotIn('[근거', result['answer'])
        self.assertEqual(result['citations'], [])
        self.assertEqual(result['audit']['answer_basis'], 'conversation')
        self.assertTrue(result['answer'].endswith('설명했습니다.'))
        recalled = recall_answer('방금 분석한 내용 중 세 번째 이유가 뭐였지?', recent_history(history, RECEIPT))
        self.assertIn('RPS 발행으로', recalled['answer'])

    def test_missing_ambiguous_or_new_fact_questions_do_not_invent_recall(self):
        question = '방금 말한 세 번째 이유가 뭐였지?'
        for history in ([], recent_history(turns('순서 없는 일반 설명입니다.')),
                        recent_history(turns('1. A\n2. B\n3. C\n1. D\n2. E\n3. F')),
                        recent_history(turns('- A\n- B\n설명\n- C\n- D\n- E'))):
            result = recall_answer(question, history)
            self.assertIn('주시기 바랍니다', result['answer'])
            self.assertEqual(result['citations'], [])
        self.assertIsNone(recall_answer('앞서 말한 세 번째 이유가 맞는지 근거로 검증해줘', turns('3. 이유')))

    def test_bullets_korean_enumeration_and_heading_recall(self):
        for answer in ('- 첫 번째\n- 두 번째\n- 세 번째',
                       '첫째, 첫 번째입니다. 둘째, 두 번째입니다. 셋째, 세 번째입니다.',
                       '### 1. 첫 번째\n### 2. 두 번째\n### 3. 세 번째',
                       '1. **첫 번째**입니다.\n2. **두 번째**입니다.\n3. **세 번째**입니다.',
                       '**1. 첫 번째**\n**2. 두 번째**\n**3. 세 번째**'):
            result = recall_answer('방금 말한 세 번째 이유가 뭐였지?', recent_history(turns(answer)))
            self.assertIn('세 번째', result['answer'])
            self.assertNotIn('주시기 바랍니다', result['answer'])

    def test_response_styles_and_polite_policy(self):
        cases = [(ANALYSIS_QUESTION, 'analysis'), ('한 문장으로 답해줘', 'one_sentence'),
                 ('짧게 답해줘', 'brief'), ('왜 불리한지 쉽게 설명해줘', 'easy'),
                 ('영향을 상세히 분석해줘', 'detailed'), ('영향이 무엇입니까?', 'conversational'),
                 ('사업보고서 핵심만 간단히 알려줘', 'brief'), ('사업보고서 제출일이 언제입니까?', 'conversational'),
                 ('공시를 보고서 형식으로 정리해줘', 'detailed')]
        for question, style in cases:
            self.assertEqual(response_style(question), style)
            self.assertIn(style + ':', ANSWER_PROMPT)
        for text in ('~습니다', '~입니다', '직접 인용', '독립적인 공시 근거가 아닙니다', '현재 number'):
            self.assertIn(text, ANSWER_PROMPT)

    def test_semantic_search_labels_previous_topic_as_unverified_context(self):
        history = recent_history(turns('PRIVATE_UNSUPPORTED_AI_FACT', '유상증자 기존 주주 영향'))
        search = contextual_search('그럼 왜 그런지 쉽게 설명해줘', history)
        self.assertIn('유상증자 기존 주주 영향', search)
        self.assertIn('미검증 문맥이며 근거가 아님', search)
        self.assertIn('PRIVATE_UNSUPPORTED_AI_FACT', search)
        self.assertEqual(contextual_search('새로운 독립 질문', history), '새로운 독립 질문')
        self.assertNotEqual(context_id(history), context_id(recent_history(turns('다른 답변'))))

    def test_actual_gms_payload_receives_ordered_history_but_not_as_evidence(self):
        history = recent_history(turns('기존 답변 [근거 99]'))
        evidence = [Evidence('doc:current:2', '현재 실제 근거', '제목', 'https://dart.fss.or.kr', 'opendart_document')]
        session = Mock()
        session.post.return_value = Mock(status_code=200, json=lambda: {'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"answer":"짧은 설명입니다. [근거 1]","supported":true}'}}]})
        client = GmsClient(Settings(openai_api_key='test', request_interval_seconds=0), session)
        result = build_graph(lambda q: evidence, client, history=history).invoke({'question': '한 문장으로 답해줘'})
        payload = json.loads(session.post.call_args.kwargs['json']['messages'][1]['content'])
        self.assertEqual(payload['history'], history)
        self.assertEqual(payload['response_style'], 'one_sentence')
        self.assertEqual([e['id'] for e in payload['evidence']], ['doc:current:2'])
        self.assertEqual(result['answer'], '짧은 설명입니다. [근거 1]')
        self.assertEqual(result['citations'][0]['id'], 'doc:current:2')

    def test_history_cannot_supply_missing_evidence_or_authorize_old_citations(self):
        history = recent_history(turns('이전 AI 주장 [근거 99]'))
        llm = Mock()
        result = build_graph(lambda q: [], llm, history=history).invoke({'question': '그 주장이 사실입니까?'})
        self.assertEqual(result['answer'], UNKNOWN)
        llm.json.assert_not_called()
        evidence = [Evidence('doc:current', '현재 근거', '제목', 'https://dart.fss.or.kr', 'opendart_document')]
        for text in ('이전 사실 주장만 있습니다.', '이전 주장은 사실입니다. [근거 99]'):
            llm.json.side_effect = lambda system, payload, validate: validate({'answer': text, 'supported': True})
            with self.assertRaises(AnswerValidationError):
                build_graph(lambda q: evidence, llm, history=history).invoke({'question': '새로운 사실은?'})

    def test_pure_recall_never_downloads_indexes_or_calls_llm(self):
        selected = Mock(rcept_no=RECEIPT)
        dependencies = [Mock() for _ in range(7)]
        service = RagService(*dependencies)
        result = service.ask(selected, '방금 말한 세 번째 이유가 뭐였지?', history=turns('1. 하나\n2. 둘\n3. 셋'))
        self.assertIn('셋', result['answer'])
        for dependency in dependencies:
            self.assertEqual(dependency.mock_calls, [])
