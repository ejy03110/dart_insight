"""Compact responsive company cards. No timers or implicit reruns."""
import streamlit as st
from dart_insight.services.recent_disclosures import latest_three
from dart_insight.ui.recent import result_notice


def render_carousel(companies, search, recent, open_detail, navigate, availability):
    # Keep the callable name for existing callers; layout now shows all companies.
    visible = [c for c in companies if not search or search.casefold() in c.name.casefold() or search in c.stock_code]
    st.subheader('기업별 최근 공시')
    if not visible:
        st.info('검색한 기업을 찾을 수 없습니다.')
        return
    with st.container(key='company_grid'):
        for column, company in zip(st.columns(len(visible)), visible):
            with column, st.container(border=True, key='company_card_' + company.stock_code):
                st.subheader(company.name)
                st.caption(company.stock_code + ' · 최근 7일')
                result = recent[company.stock_code]
                result_notice(result)
                for disclosure in latest_three(result):
                    ready, reason = availability(disclosure)
                    state = '본문 준비됨' if ready and '재사용' in reason else '자동 준비·분석' if ready else '연결 설정 필요'
                    st.button(disclosure.report_nm, key='recent_home_' + company.stock_code + '_' + disclosure.rcept_no,
                              on_click=open_detail, args=(disclosure,), width='stretch')
                    st.caption(f'{disclosure.rcept_dt} · {state}')
                st.button('공시 살펴보기', key='company_' + company.stock_code,
                          on_click=navigate, args=('공시 탐색', None, company.name))
