"""Conservative checks for three explicit financial claim patterns, not general NLI.

Only actually cited original-document excerpts can support these assertions.
Unknown wording is outside coverage; ambiguous recognized claims fail closed.
"""
import re
from decimal import Decimal

from dart_insight.rag.validation import AnswerValidationError

CITATION = re.compile(r'\[근거\s*(\d+)\]')
OWNER = r'(?:기존주주|일반주주|구주주)'
SUBSCRIPTION = re.compile(OWNER + r'.{0,65}?(?:청약기회|청약권리|청약권|신주인수권|청약에참여|신주를청약)(.*)')
ALLOCATION = re.compile(r'(?:' + OWNER + r'.{0,50}?(?:신주.{0,12}배정|배정대상)(.*)|배정대상자?(?:는|:|：)' + OWNER + r'(.*))')
RESTRICTION = re.compile(r'(?:' + OWNER + r'|기존주식|기보유주식)(.{0,65}?)(의무보유|보호예수|매도제한|매각제한|매매제한|매도할수없|매각할수없|팔수없)(.*)')
PURPOSES = {
    'operating': r'운영자금|운영비',
    'facilities': r'시설자금|시설투자자금',
    'debt': r'채무상환자금|채무상환|부채상환|차입금상환',
    'securities': r'타법인증권취득자금|타법인주식취득자금|타법인증권취득',
    'acquisition': r'영업양수자금|영업양수',
    'other': r'기타자금',
}
PURPOSE = re.compile('|'.join('(?:' + pattern + ')' for pattern in PURPOSES.values()))
USE = re.compile(r'사용|활용|투입|충당|조달|목적|용도|계획')


def compact(text):
    return re.sub(r'\s+|\*\*|`', '', text)


def denied(text):
    # Scope to the predicate AFTER the relevant subject/object, not the whole answer.
    return bool(re.search(r'없|불가|아니|않|미확인|미정|확인(?:되지|할수없)|명시되지|근거부족|제외', text))


def clauses(text):
    return [compact(s) for s in re.split(r'\n+|(?<=[.!?。])\s+', text) if s.strip()]


def positive_owner_claim(clause, patterns=(SUBSCRIPTION, ALLOCATION)):
    for pattern in patterns:
        match = pattern.search(clause)
        if match and not denied(''.join(g or '' for g in match.groups())):
            return True
    return False


def positive_old_holder_restriction(clause):
    match = RESTRICTION.search(clause)
    if not match:
        return False
    link, restriction, predicate = match.groups()
    # "기존 주주는 신주 인수자의 의무보유로 ..." describes another actor.
    if re.search(r'인수자|인수인|배정대상자|신주의|신주에대한|신규발행주식', link):
        return False
    # "매도할 수 없습니다" is itself the restriction, not its denial.
    return not denied(predicate)


def purpose_support(text, category):
    """True/False for explicit positive/zero/negative purpose; None if unconfirmed."""
    pattern = re.compile(PURPOSES[category])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    states = []
    for index, line in enumerate(lines):
        match = pattern.search(compact(line))
        if not match:
            continue
        normalized = compact(line)
        tail = normalized[match.end():]
        # Recognize a simple field:value or field + next-line numeric table cell.
        cell = re.sub(r'^\([^)]*\)', '', tail).lstrip(':：')
        if not cell and index + 1 < len(lines):
            cell = compact(lines[index + 1])
        numeric = re.fullmatch(r'([+-]?\d[\d,]*(?:\.\d+)?)(?:원|천원|백만원|억원)?', cell)
        if numeric:
            states.append(Decimal(numeric[1].replace(',', '')) > 0)
        elif cell in ('-', '—', '없음', '해당없음', '계획없음', '미정'):
            states.append(False)
    if states:
        return all(states)  # Conflicting correction/table values are not silently resolved.
    for clause in clauses(text):
        if not re.search(r'조달자금|증자자금|증자대금|조달목적|자금조달의?목적|자금의?(?:사용)?(?:목적|용도)', clause):
            continue
        match = pattern.search(clause)
        if match and USE.search(clause):
            tail = clause[match.end():]
            # Do not let the next category's negative predicate negate this one.
            following = PURPOSE.search(tail)
            if following:
                tail = tail[:following.start()]
                if not USE.search(tail):
                    continue  # Shared predicates across several purposes are ambiguous.
            if denied(tail):
                states.append(False)
            elif re.search(r'사용|활용|투입|충당|(?:목적|용도).{0,20}(?:입니다|한다|이다)|자금조달의?목적|조달목적|조달자금', clause):
                states.append(True)
    return all(states) if states else None


def validate_core_claims(answer, evidence):
    def check(block, numbers):
        originals = [evidence[n - 1].text for n in numbers
                     if evidence[n - 1].source_type == 'opendart_document']
        source_clauses = [c for text in originals for c in clauses(text)]
        for clause in clauses(block):
            for claim_pattern in (SUBSCRIPTION, ALLOCATION):
                if positive_owner_claim(clause, (claim_pattern,)) and not any(positive_owner_claim(c, (claim_pattern,)) for c in source_clauses):
                    raise AnswerValidationError('grounding', 'answer', 'shareholder_allocation_not_supported')
            if positive_old_holder_restriction(clause) and not any(positive_old_holder_restriction(c) for c in source_clauses):
                raise AnswerValidationError('grounding', 'answer', 'existing_holder_restriction_not_supported')
            if not USE.search(clause):
                continue
            for category, pattern in PURPOSES.items():
                match = re.search(pattern, clause)
                if not match:
                    continue
                tail = clause[match.end():]
                following = PURPOSE.search(tail)
                if following:
                    tail = tail[:following.start()]
                if denied(tail):
                    continue
                states = [purpose_support(text, category) for text in originals]
                if True not in states or False in states:
                    raise AnswerValidationError('grounding', 'answer', 'funding_purpose_not_supported')
    position = 0
    for match in re.finditer(r'(?:\[근거\s*\d+\][ \t]*)+', answer):
        check(answer[position:match.start()], [int(n) for n in CITATION.findall(match[0])])
        position = match.end()
    # Recognized assertions after the final citation cannot borrow an earlier source.
    check(answer[position:], [])
