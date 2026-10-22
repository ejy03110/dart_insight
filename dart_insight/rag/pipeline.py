"""F2007/F2008: actual LangGraph retrieve -> generate with citation validation."""
import re
from dart_insight.diagnostics import measure
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from dart_insight.rag.prompts.answer import ANSWER_PROMPT
from dart_insight.rag.conversation import recent_history, response_style
from dart_insight.rag.validation import AnswerValidationError, object_fields

UNKNOWN = '제공된 문서에서 확인되지 않습니다.'


class RagState(TypedDict, total=False):
    question: str
    evidence: list
    answer: str
    citations: list
    history: list
    response_style: str


def validate_answer(value, evidence, question=''):
    object_fields(value, ('answer', 'supported'), AnswerValidationError)
    if type(value['supported']) is not bool:
        raise AnswerValidationError('schema', 'supported', 'boolean_required')
    if not isinstance(value['answer'], str):
        raise AnswerValidationError('schema', 'answer', 'string_required')
    if not value['answer'].strip():
        raise AnswerValidationError('schema', 'answer', 'nonempty_string')
    if not value['supported']:
        return {'answer': UNKNOWN, 'citations': []}
    numbers = list(dict.fromkeys(int(n) for n in re.findall(r'\[근거\s*(\d+)\]', value['answer'])))
    if not numbers:
        raise AnswerValidationError('citation', 'answer', 'citation_required')
    if any(n < 1 or n > len(evidence) for n in numbers):
        raise AnswerValidationError('citation', 'answer', 'citation_number_out_of_range')
    # Reject malformed citation tokens rather than displaying fabricated references.
    remaining = re.sub(r'\[근거\s*\d+\]', '', value['answer'])
    if '[근거' in remaining:
        raise AnswerValidationError('citation', 'answer', 'citation_token_format')
    if re.search(r'취득.*(?:원인|목적|이유)', question):
        if re.search(r'(?:목적|이유|원인)\s*[:：]\s*(?:장내매수|장외매수|시간외매수)', value['answer']):
            raise AnswerValidationError('grounding', 'answer', 'acquisition_method_is_not_purpose')
        explicit_purpose = any(re.search(r'취득\s*(?:의\s*)?(?:목적|이유)', e.text) for e in evidence
                               if e.source_type == 'opendart_document')
        if not explicit_purpose and not re.search(
                r'목적.{0,200}?(?:확인\s*(?:되지|할\s*수\s*없|불가|불가능)|명시되어 있지|명시되지|기재되지)',
                value['answer'], re.S):
            raise AnswerValidationError('grounding', 'answer', 'missing_purpose_acknowledgement')
    return {'answer': value['answer'],
            'citations': [{'number': n, **evidence[n - 1].to_dict()} for n in numbers]}


def build_graph(retriever, llm, progress=lambda _: None, *, history=None, receipt=None):
    recent = recent_history(history, receipt)
    def retrieve_node(state):
        progress('관련 근거 검색 중')
        with measure('rag_retrieval'):
            return {'evidence': retriever(state['question'])}

    def generate_node(state):
        progress('GMS 답변 생성 중')
        evidence = state['evidence']
        if not evidence:
            return {'answer': UNKNOWN, 'citations': []}
        payload = {'question': state['question'],
                   'history': recent_history(state.get('history', recent), receipt), 'response_style': response_style(state['question']),
                   'evidence': [{'number': n, **e.to_dict()} for n, e in enumerate(evidence, 1)]}
        return llm.json(ANSWER_PROMPT, payload, lambda v: validate_answer(v, evidence, state['question']))

    builder = StateGraph(RagState)
    builder.add_node('retrieve', retrieve_node)
    builder.add_node('generate', generate_node)
    builder.add_edge(START, 'retrieve')
    builder.add_edge('retrieve', 'generate')
    builder.add_edge('generate', END)
    return builder.compile()
