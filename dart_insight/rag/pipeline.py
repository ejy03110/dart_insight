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


def validate_answer(value, evidence):
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
        return llm.json(ANSWER_PROMPT, payload, lambda v: validate_answer(v, evidence))

    builder = StateGraph(RagState)
    builder.add_node('retrieve', retrieve_node)
    builder.add_node('generate', generate_node)
    builder.add_edge(START, 'retrieve')
    builder.add_edge('retrieve', 'generate')
    builder.add_edge('generate', END)
    return builder.compile()
