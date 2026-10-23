"""One exploration surface, preserving API server pagination and indexed discovery."""
import streamlit as st
from dart_insight.data.models import DISCLOSURE_TYPES
from dart_insight.services import discovery
from dart_insight.services.discovery import interpret_condition, empty_message
from dart_insight.rag.llm import GmsClient
from dart_insight.ui.search import render_search


def stored_matches(disclosure, query):
    if query is None:
        return True
    company = query.company.casefold()
    return (not company or company in disclosure.corp_name.casefold() or company in (disclosure.stock_code, disclosure.corp_code)) and (
        query.begin.isoformat() <= disclosure.rcept_dt <= query.end.isoformat()) and (
        not query.kind or disclosure.disclosure_type in (query.kind, DISCLOSURE_TYPES.get(query.kind))) and (
        not query.keyword or query.keyword.casefold() in disclosure.report_nm.casefold())


def render_unified(settings, indexed, metadata, embeddings, open_detail, availability):
    def supplement(query, api_items):
        disclosures = list(indexed.values())
        if disclosures:
            dates = sorted(d.rcept_dt for d in disclosures)
            companies = ', '.join(sorted({d.corp_name for d in disclosures}))
            st.caption(f'저장 데이터 범위: {companies} · 제출일 {dates[0]} ~ {dates[-1]}. API는 입력한 기업·기간만 조회합니다. 범위 밖 자료를 합쳐 검색한 것으로 간주하지 않습니다.')
        options = [d for d in disclosures if stored_matches(d, query)]
        with st.expander('자연어 조건으로 공시 찾기'):
            st.caption('자연어 조건은 저장된 메타데이터에 적용합니다. API 전체 결과나 본문 수치까지 검색하는 기능은 아닙니다.')
            with st.form('discovery'):
                condition = st.text_area('검색 조건', placeholder='2025년 삼성전자 사업보고서, 정정 제외')
                find = st.form_submit_button('조건에 맞는 공시 검색')
            signature = (repr(query), tuple(sorted(d.rcept_no for d in options)))
            if find:
                st.session_state['discovery_condition'] = condition
                st.session_state['discovery_signature'] = signature
                st.session_state.pop('discovery_error', None)
                st.session_state['discovery_results'] = []
                if not condition.strip():
                    st.session_state['discovery_error'] = '검색 조건을 입력해 주세요.'
                elif not settings.openai_api_key:
                    st.session_state['discovery_error'] = 'AI 검색을 이용하려면 서비스 인증 설정이 필요합니다. 관리자에게 문의해 주세요.'
                elif metadata is None:
                    st.session_state['discovery_error'] = '검색 가능한 저장 색인이 없습니다.'
                else:
                    try:
                        with st.status('조건 후보 검색·선별 중'):
                            llm = GmsClient(settings)
                            try:
                                st.session_state['discovery_results'] = discovery.discover(metadata, condition, embeddings, settings, llm, receipt_scope={d.rcept_no for d in options})
                            finally:
                                llm.close()
                    except Exception:
                        st.session_state['discovery_error'] = '조건 검색에 실패했습니다. 결과 없음과 다릅니다. 잠시 후 다시 시도해 주세요.'
            active = False
            if st.session_state.get('discovery_signature') == signature:
                if st.session_state.get('discovery_error'):
                    st.error(st.session_state['discovery_error'])
                elif 'discovery_results' in st.session_state:
                    active = True
                    def clear_condition():
                        for name in ('discovery_results', 'discovery_signature', 'discovery_error'):
                            st.session_state.pop(name, None)
                    st.button('자연어 조건 해제', key='clear_discovery', on_click=clear_condition)
                    condition = st.session_state.get('discovery_condition', '')
                    st.caption(interpret_condition(condition).description)
                    allowed = {d.rcept_no for d in options}
                    matches = [row['metadata'] for row in st.session_state['discovery_results'] if row['metadata']['rcept_no'] in allowed]
                    if not matches:
                        st.info(empty_message(condition))
                    for row in matches:
                        d = indexed[row['rcept_no']]
                        st.caption(availability(d)[1])
                        st.button(f'{d.corp_name} · {d.report_nm} · {d.rcept_dt}', key='result_' + d.rcept_no,
                                  on_click=open_detail, args=(d, 'stored'))
        if active:
            st.info('자연어 조건은 저장 색인에만 적용했습니다. 공식 API 결과는 조건 일치를 검증하지 않았으므로 함께 표시하지 않습니다.')
            return True
        api_ids = {d.rcept_no for d in api_items}
        extra = sorted((d for d in options if d.rcept_no not in api_ids), key=lambda d: (d.rcept_dt, d.rcept_no), reverse=True)
        st.subheader('함께 찾은 저장 공시')
        st.caption(f'동일 검색 조건의 저장 공시 {len(options):,}건 · 현재 API 페이지와 중복되는 공시는 API 목록에만 표시합니다. 아래 페이지는 저장 결과에만 적용됩니다.')
        if not extra:
            st.info('조건에 맞는 추가 저장 공시가 없습니다.')
            return
        pages = list(range(1, (len(extra)-1)//20+2))
        query_key = repr(query)
        if '_stored_query' in st.session_state and st.session_state['_stored_query'] != query_key:
            st.session_state['stored_page'] = 1
        st.session_state['_stored_query'] = query_key
        if st.session_state.get('stored_page', 1) not in pages:
            st.session_state['stored_page'] = 1
        from dart_insight.ui import routes
        number = st.selectbox('저장 결과 페이지', pages, key='stored_page', on_change=routes.write_route)
        for disclosure in extra[(number-1)*20:number*20]:
            with st.container(border=True):
                st.subheader(disclosure.report_nm)
                st.caption(f'{disclosure.corp_name} · {disclosure.rcept_dt} · {disclosure.disclosure_type}')
                st.caption(availability(disclosure)[1])
                st.button('공시 상세 보기', key='open_' + disclosure.rcept_no,
                          on_click=open_detail, args=(disclosure, 'stored'))
    render_search(settings, indexed, open_detail, supplement=supplement, availability=availability)
