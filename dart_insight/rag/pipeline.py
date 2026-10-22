"""F2007/F2008: actual LangGraph retrieve -> generate with citation validation."""
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from dart_insight.rag.prompts.answer import ANSWER_PROMPT

UNKNOWN = '제공된 문서에서 확인되지 않습니다.'


class RagState(TypedDict, total=False):
    question: str
    evidence: list
    answer: str
    citations: list


def validate_answer(value, evidence, question=''):
    if not isinstance(value, dict) or set(value) != {'answer', 'supported'}:
        raise ValueError('Invalid answer schema')
    if type(value['supported']) is not bool or not isinstance(value['answer'], str) or not value['answer'].strip():
        raise ValueError('Invalid answer')
    if not value['supported']:
        return {'answer': UNKNOWN, 'citations': []}
    numbers = list(dict.fromkeys(int(n) for n in re.findall(r'\[근거\s*(\d+)\]', value['answer'])))
    if not numbers or any(n < 1 or n > len(evidence) for n in numbers):
        raise ValueError('Unknown or missing citations')
    # Reject malformed citation tokens rather than displaying fabricated references.
    remaining = re.sub(r'\[근거\s*\d+\]', '', value['answer'])
    if '[근거' in remaining:
        raise ValueError('Invalid citation token')
    if re.search(r'취득.*(?:원인|목적|이유)', question):
        if re.search(r'(?:목적|이유|원인)\s*[:：]\s*(?:장내매수|장외매수|시간외매수)', value['answer']):
            raise ValueError('Acquisition method cannot serve as purpose')
        explicit_purpose = any(re.search(r'취득\s*(?:의\s*)?(?:목적|이유)', e.text) for e in evidence
                               if e.source_type == 'opendart_document')
        if not explicit_purpose and not re.search(
                r'목적.{0,200}?(?:확인\s*(?:되지|할\s*수\s*없|불가|불가능)|명시되어 있지|명시되지|기재되지)',
                value['answer'], re.S):
            raise ValueError('Missing acquisition purpose must be acknowledged')
    return {'answer': value['answer'],
            'citations': [{'number': n, **evidence[n - 1].to_dict()} for n in numbers]}


def build_graph(retriever, llm, progress=lambda _: None):
    def retrieve_node(state):
        progress('질문 관련 근거 검색 중')
        return {'evidence': retriever(state['question'])}

    def generate_node(state):
        progress('근거 기반 답변 생성 중')
        evidence = state['evidence']
        if not evidence:
            return {'answer': UNKNOWN, 'citations': []}
        payload = {'question': state['question'],
                   'evidence': [{'number': n, **e.to_dict()} for n, e in enumerate(evidence, 1)]}
        return llm.json(ANSWER_PROMPT, payload, lambda v: validate_answer(v, evidence, state['question']))

    builder = StateGraph(RagState)
    builder.add_node('retrieve', retrieve_node)
    builder.add_node('generate', generate_node)
    builder.add_edge(START, 'retrieve')
    builder.add_edge('retrieve', 'generate')
    builder.add_edge('generate', END)
    return builder.compile()
