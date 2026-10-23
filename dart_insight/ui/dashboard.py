"""One responsive company card at a time; no timers or automatic reruns."""
import streamlit as st
from dart_insight.services.recent_disclosures import latest_three
from dart_insight.ui.recent import result_notice


def move_company(step, count):
    st.session_state['company_slide'] = (st.session_state.get('company_slide', 0) + step) % count


def render_carousel(companies, search, recent, open_detail, navigate, availability):
    visible = [c for c in companies if not search or search.casefold() in c.name.casefold() or search in c.stock_code]
    st.subheader('기업별 최근 공시')
    if not visible:
        st.info('검색한 기업을 찾을 수 없습니다.')
        return
    index = st.session_state.get('company_slide', 0) % len(visible)
    company = visible[index]
    with st.container(horizontal=True, wrap=True):
        st.button('← 이전 기업', key='company_previous', on_click=move_company, args=(-1, len(visible)), disabled=len(visible)==1)
        st.caption(f'{index+1} / {len(visible)} · {company.name}')
        st.button('다음 기업 →', key='company_next', on_click=move_company, args=(1, len(visible)), disabled=len(visible)==1)
    with st.container(border=True, key='company_card_' + company.stock_code):
        st.subheader(company.name)
        st.caption(company.stock_code + ' · 오늘 포함 최근 7일')
        result = recent[company.stock_code]
        result_notice(result)
        for disclosure in latest_three(result):
            st.button(disclosure.report_nm, key='recent_home_' + company.stock_code + '_' + disclosure.rcept_no,
                      on_click=open_detail, args=(disclosure,))
            st.caption(f'{disclosure.rcept_dt} · {disclosure.disclosure_type} · {availability(disclosure)[1]}')
        st.button('공시 살펴보기', key='company_' + company.stock_code,
                  on_click=navigate, args=('공시 탐색', None, company.name))
