"""Recent metadata UI; the home screen opts into cache-coalesced auto refresh."""
import streamlit as st
from dart_insight.services.recent_disclosures import RecentDisclosureService


@st.cache_resource(show_spinner=False)
def recent_service(settings):
    return RecentDisclosureService(settings)


def recent_controls(settings, *, auto=False):
    service = recent_service(settings)
    results = service.peek()
    loaded = any(r.state != 'unloaded' for r in results.values())
    label = '최근 공시 새로고침' if loaded else '최근 공시 불러오기'
    refresh = st.button(label,key='load_recent')
    if auto or refresh:
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


def _remember_recent(name):
    st.session_state[name]=st.session_state['_'+name]
    if name!='recent_page':
        st.session_state['recent_page']=1


def render_recent_list(settings, indexed, open_recent):
    st.caption('최근 7일 조회 결과입니다. 보관 여부를 확인한 공시만 AI 분석에 연결합니다.')
    results = recent_controls(settings)
    for name,default in [('recent_search',''),('recent_company','전체'),('recent_type','전체')]:
        st.session_state['_'+name]=st.session_state.get(name,default)
    search=st.text_input('기업명 또는 종목코드 검색',key='_recent_search',placeholder='예: 삼성전자, 005930',
                         on_change=_remember_recent,args=('recent_search',)).strip()
    from datetime import date
    companies=['전체',*[r.company.name for r in results.values()]]
    types=['전체',*sorted({d.disclosure_type for r in results.values() for d in r.disclosures})]
    if st.session_state['_recent_type'] not in types:
        st.session_state['_recent_type']='전체'
    columns=st.columns(3)
    with columns[0]:
        company=st.selectbox('기업',companies,key='_recent_company',on_change=_remember_recent,args=('recent_company',))
    with columns[1]:
        kind=st.selectbox('공시 유형',types,key='_recent_type',on_change=_remember_recent,args=('recent_type',))
    begin=min(r.begin for r in results.values());end=max(r.end for r in results.values())
    dates=st.session_state.get('recent_dates',(begin,end))
    if any(not begin<=d<=end for d in dates):
        dates=(begin,end)
    st.session_state['_recent_dates']=dates
    with columns[2]:
        dates=st.date_input('공시 기간',key='_recent_dates',min_value=begin,max_value=end,
                            on_change=_remember_recent,args=('recent_dates',))
    rows=[]
    for code,result in results.items():
        if company!='전체' and company!=result.company.name:
            continue
        if search and search.casefold() not in result.company.name.casefold() and search not in code:
            continue
        st.subheader(result.company.name)
        result_notice(result)
        rows.extend(d for d in result.disclosures if (kind=='전체' or d.disclosure_type==kind)
                    and len(dates)==2 and dates[0]<=date.fromisoformat(d.rcept_dt)<=dates[1])
    rows.sort(key=lambda d:(d.rcept_dt,d.rcept_no),reverse=True)
    if not rows:
        if any(r.state=='success' for r in results.values()):
            st.info('조건에 맞는 공시가 없습니다.')
        return
    pages=list(range(1,(len(rows)-1)//20+2))
    number=st.session_state.get('recent_page',1)
    st.session_state['_recent_page']=number if number in pages else 1
    number=st.selectbox('목록 페이지',pages,key='_recent_page',on_change=_remember_recent,args=('recent_page',))
    for disclosure in rows[(number-1)*20:number*20]:
        with st.container(border=True):
            st.write(disclosure.report_nm)
            st.caption(f'{disclosure.corp_name} · {disclosure.rcept_dt}')
            known = indexed.get(disclosure.rcept_no)
            ready = known is not None and known.corp_code == disclosure.corp_code
            st.caption('보관된 공시 · AI 분석 가능' if ready else '미색인 공시 · 원문 확인만 가능')
            st.link_button('공식 DART 원문 보기 ↗',disclosure.source_url)
            st.button('공시 상세 보기',key='recent_list_'+disclosure.rcept_no,
                      on_click=open_recent,args=(disclosure,))
