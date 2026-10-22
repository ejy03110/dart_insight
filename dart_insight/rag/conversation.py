"""Bounded, session-only context; conversation recall is never disclosure evidence."""
import hashlib
import json
import re

MAX_PAIRS = 6
MAX_CHARACTERS = 12000


def recent_history(turns, receipt=None):
    pairs = []
    turns = list(turns or [])
    for index in range(0, len(turns) - 1, 2):
        pair = turns[index:index + 2]
        if (not all(isinstance(t, dict) for t in pair) or
                [t.get('role') for t in pair] != ['user', 'assistant'] or
                any(not isinstance(t.get('content'), str) or not t['content'].strip() for t in pair) or
                any(receipt is not None and t.get('rcept_no', receipt) != receipt for t in pair)):
            # A broken/foreign suffix cannot silently refer to an older answer instead.
            pairs = []
            continue
        pairs.append([{'role': t['role'], 'content': t['content']} for t in pair])
    if len(turns) % 2:
        pairs = []
    selected, size = [], 0
    for pair in reversed(pairs[-MAX_PAIRS:]):
        length = sum(len(t['content']) for t in pair)
        if size + length > MAX_CHARACTERS:
            break  # Never cut an answer/list in half or skip the latest oversized pair.
        selected.insert(0, pair)
        size += length
    return [turn for pair in selected for turn in pair]


def context_id(history):
    return hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:24]


def response_style(question):
    from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
    if question == ANALYSIS_QUESTION:
        return 'analysis'
    if re.search(r'한\s*(?:문장|줄)|1\s*문장', question):
        return 'one_sentence'
    if re.search(r'짧게|간단히|핵심만', question):
        return 'brief'
    if re.search(r'상세|자세히|심층|보고서\s*(?:형식|처럼|로\s*(?:작성|정리))', question):
        return 'detailed'
    if re.search(r'쉽게|쉬운\s*말|쉬운\s*표현', question):
        return 'easy'
    return 'conversational'


def contextual_search(question, history):
    if not history or not re.search(r'그거|그럼|그것|방금|앞서|이전|말한|설명한|더\s*쉽게|세\s*번째', question):
        return question
    # Previous prose may identify a search topic, but is never indexed or added as evidence.
    previous = [t['content'] for t in history if t['role'] == 'user'][-2:]
    topic = re.sub(r'\[근거\s*\d+\]', '', history[-1]['content'])[:2000]
    return '\n'.join(['최근 사용자 질문: ' + t[:1000] for t in previous] +
                     ['이전 설명의 검색 주제(미검증 문맥이며 근거가 아님): ' + topic, '현재 질문: ' + question])


def recall_ordinal(question):
    if (not re.search(r'방금|앞서|이전|직전|아까|말한|설명한|네가', question) or
            not re.search(r'뭐|무엇|다시|기억', question) or
            re.search(r'검증|분석해|분석하여|분석하[라세]|맞는지|사실인지|왜|영향|근거', question)):
        return None
    match = re.search(r'(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|[1-9]|10)\s*번째\s*(?:이유|항목|설명|내용)', question)
    if not match:
        return None
    words = ['첫', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열']
    return int(match[1]) if match[1].isdigit() else words.index(match[1]) + 1


def recall_answer(question, history):
    number = recall_ordinal(question)
    if number is None:
        return None
    if not history:
        text = '이 공시의 이전 대화에서 해당 항목을 확인할 수 없습니다. 어느 설명을 말씀하시는지 알려주시기 바랍니다.'
    else:
        answer = history[-1]['content']
        # Unique explicit numbering only; ambiguous/unstructured lists require clarification.
        matches = list(re.finditer(r'^\s{0,3}(?:#{1,6}\s*)?(?:\*\*)?(\d{1,2})[.)]\s+(.+)$', answer, re.M))
        candidates = []
        if matches and [int(m[1]) for m in matches] == list(range(1, len(matches) + 1)):
            for match in matches:
                if int(match[1]) == number:
                    item = match[2].strip()
                    if re.match(r'^\s*(?:#{1,6}\s*)?\*\*', match[0]):
                        item = item.replace('**', '', 1).strip()
                    candidates.append(item)
        if not matches:
            # Korean enumerations may appear inline (첫째, ... 둘째, ... 셋째, ...).
            labels = ['첫째', '둘째', '셋째', '넷째', '다섯째', '여섯째', '일곱째', '여덟째', '아홉째', '열째']
            korean = list(re.finditer(r'(?:^|\s)(' + '|'.join(labels) + r')[,:：]\s*', answer))
            if korean and [labels.index(m[1]) + 1 for m in korean] == list(range(1, len(korean) + 1)):
                if number <= len(korean):
                    stop = korean[number].start() if number < len(korean) else len(answer)
                    candidates = [answer[korean[number - 1].end():stop].strip()]
            elif not korean:
                groups, current = [], []
                for line in answer.splitlines():
                    bullet = re.match(r'^ {0,3}[-*]\s+(.+)$', line)
                    if bullet:
                        current.append(bullet[1])
                    elif line.strip() and current:
                        groups.append(current)
                        current = []
                if current:
                    groups.append(current)
                if len(groups) == 1:
                    candidates = groups[0][number - 1:number]
        if candidates and (not candidates[0].strip() or len(candidates[0]) > 600):
            candidates = []
        if len(candidates) != 1:
            text = '이전 답변에서 해당 순서의 항목을 명확히 구분할 수 없습니다. 해당 항목을 지정해 주시기 바랍니다.'
        else:
            quote = re.sub(r'\[근거\s*\d+\]', '', candidates[0]).strip()
            ordinal = ['첫', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉', '열'][number - 1]
            label = '이유' if '이유' in question else '항목'
            text = f'이전 답변에서는 {ordinal} 번째 {label}로 “{quote}”라고 설명했습니다.'
    return {'answer': text, 'citations': [], 'audit': {'answer_basis': 'conversation',
            'history_messages': len(history), 'recalled_ordinal': number}}
