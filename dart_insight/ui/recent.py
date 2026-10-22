"""Recent disclosure UI. Only the explicit refresh button invokes the service."""
import streamlit as st
from dart_insight.services.recent_disclosures import RecentDisclosureService


@st.cache_resource(show_spinner=False)
def recent_service(settings):
    return RecentDisclosureService(settings)


def recent_controls(settings):
    service = recent_service(settings)
    results = service.peek()
    loaded = any(r.state != 'unloaded' for r in results.values())
    label = '최근 공시 새로고침' if loaded else '최근 공시 불러오기'
    if st.button(label,key='load_recent'):
        with st.spinner('공시를 확인하고 있습니다.'):
            results = service.refresh()
    st.caption('오늘 포함 최근 7일 · 조회 결과는 최대 5분간 재사용합니다. 조회 실패 시 1분 후 다시 시도할 수 있습니다.')
    return results


def result_notice(result):
    if result.checked_at:
        st.caption('마지막 조회: '+result.checked_at.strftime('%m.%d %H:%M')+' (한국 시간)')
    if result.state == 'empty':
        st.info('최근 7일간 새로운 공시가 없습니다.')
    elif result.state == 'error':
        st.warning('최근 공시를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.')
    elif result.state == 'unloaded':
        st.caption('최근 공시를 불러오면 이곳에 표시됩니다.')


def render_recent_list(settings, indexed, open_recent):
    st.caption('최근 7일 조회 결과입니다. 보관 여부를 확인한 공시만 AI 분석에 연결합니다.')
    search = st.text_input('기업명 또는 종목코드 검색', key='recent_search', placeholder='예: 삼성전자, 005930').strip()
    results = recent_controls(settings)
    for code,result in results.items():
        if search and search.casefold() not in result.company.name.casefold() and search not in code:
            continue
        st.subheader(result.company.name)
        result_notice(result)
        for disclosure in result.disclosures:
            with st.container(border=True):
                st.write(disclosure.report_nm)
                st.caption(f'{disclosure.corp_name} · {disclosure.rcept_dt}')
                known = indexed.get(disclosure.rcept_no)
                ready = known is not None and known.corp_code == disclosure.corp_code
                st.caption('보관된 공시 · AI 분석 가능' if ready else '미색인 공시 · 원문 확인만 가능')
                st.link_button('공식 DART 원문 보기 ↗',disclosure.source_url)
                st.button('공시 상세 보기',key='recent_list_'+disclosure.rcept_no,
                          on_click=open_recent,args=(disclosure,))
