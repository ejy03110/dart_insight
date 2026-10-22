"""F2011: explicit year semantics + indexed-data candidates + LLM condition selection."""
from dataclasses import dataclass
import re

from dart_insight.rag.prompts.selection import DISCOVERY_PROMPT, selected_ids
from dart_insight.vectorstore.metadata import query_metadata


@dataclass(frozen=True)
class SearchCriteria:
    years: tuple[int, ...]
    fiscal: bool
    company: str | None
    report_type: str | None
    numeric_body: bool
    year_range: bool = False

    @property
    def description(self):
        if not self.years:
            return '연도 제한 없음. 공시명 괄호의 연도는 사업연도, 접수일은 제출일입니다.'
        basis = '사업연도(공시명 괄호의 결산기간)' if self.fiscal else '제출연도(공시 접수일)'
        years = ' ~ '.join(map(str, self.years)) if self.year_range else ', '.join(map(str, self.years))
        return f'검색 기준: {basis} {years}년. 사업연도를 원하면 “2024 사업연도”처럼 입력해 주세요.'

    def matches(self, metadata):
        if self.company and metadata.get('corp_name') != self.company:
            return False
        if self.report_type and self.report_type not in metadata.get('report_nm', ''):
            return False
        if self.years:
            match = (re.search(r'\(\s*((?:19|20)\d{2})[.\-/]\d{1,2}\s*\)', metadata.get('report_nm', ''))
                     if self.fiscal else re.match(r'((?:19|20)\d{2})-', metadata.get('rcept_dt', '')))
            if not match:
                return False
            year = int(match[1])
            if self.year_range:
                if not min(self.years) <= year <= max(self.years):
                    return False
            elif year not in self.years:
                return False
        return True


def interpret_condition(condition):
    years = tuple(dict.fromkeys(int(y) for y in re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', condition)))
    fiscal = bool(re.search(r'사업\s*연도|회계\s*연도|결산\s*연도|귀속\s*연도|\(\s*(?:19|20)\d{2}\.\d{2}\s*\)', condition))
    # A bare '2025년 사업보고서' means a filing submitted in 2025, NOT fiscal 2025.
    if re.search(r'제출|접수|공시\s*연도', condition):
        fiscal = False
    company = next((canonical for alias, canonical in
                    [('삼성전자', '삼성전자'), ('현대자동차', '현대자동차'), ('현대차', '현대자동차'), ('금양', '금양')]
                    if alias in condition), None)
    report = next((kind for kind in ('사업보고서', '반기보고서', '분기보고서') if kind in condition), None)
    body_term = re.search(r'매출|영업이익|순이익|부채|자산|취득\s*수량|보유\s*수량|지분율|금액|수익률|배당|자본금', condition)
    numeric = bool(body_term and re.search(r'\d+\s*(?:억|조|만|원|%|주|배)|이상|이하|초과|미만|흑자|적자', condition))
    return SearchCriteria(years, fiscal, company, report, numeric,
                          len(years) > 1 and bool(re.search(r'~|부터|사이|에서', condition)))


def empty_message(condition):
    criteria = interpret_condition(condition)
    if criteria.numeric_body:
        return '본문 수치 조건은 메타데이터만으로 확인할 수 없습니다. 공시를 선택한 뒤 원문에 질문해 주세요.'
    return '현재 색인된 공시 중 조건에 맞는 결과가 없습니다. 제출연도·사업연도와 기업·공시 종류를 확인해 주세요.'


def discover(collection, condition, embeddings, settings, llm, corp_code=None):
    if not condition.strip():
        raise ValueError('검색할 자연어 조건을 입력해 주세요.')
    criteria = interpret_condition(condition)
    if criteria.numeric_body:
        return []  # No numeric body fields exist in the metadata collection.
    rows = query_metadata(collection, condition, embeddings, model=settings.embedding_model,
                          corp_code=corp_code, top_k=settings.candidate_top_k)
    # Year/title/company filters must run over the actual index, not just the first
    # few semantic hits, otherwise a matching filing can be lost before selection.
    if criteria.years or criteria.company or criteria.report_type:
        indexed = []
        for offset in range(0, collection.count(), 1000):
            kwargs = {'where': {'corp_code': corp_code}} if corp_code else {}
            page = collection.get(limit=1000, offset=offset, include=['documents', 'metadatas'], **kwargs)
            indexed.extend({'id': i, 'text': text, 'metadata': m} for i, text, m in
                           zip(page['ids'], page['documents'], page['metadatas']) if criteria.matches(m))
        found = {r['metadata']['rcept_no']: r for r in rows if criteria.matches(r['metadata'])}
        # Keep semantic ordering first; supplement with qualifying indexed records.
        for row in sorted(indexed, key=lambda r: r['metadata'].get('rcept_dt', ''), reverse=True):
            found.setdefault(row['metadata']['rcept_no'], row)
        rows = list(found.values())[:settings.candidate_top_k]
    if not rows:
        return []
    candidates = {row['metadata']['rcept_no']: row for row in rows}
    chosen = llm.json(DISCOVERY_PROMPT,
                      {'condition': condition, 'interpretation': criteria.description,
                       'candidates': [r['metadata'] for r in rows]},
                      lambda value: selected_ids(value, candidates))
    # Recheck explicit constraints even if the model violates its instructions.
    return [candidates[receipt] for receipt in chosen if criteria.matches(candidates[receipt]['metadata'])]
