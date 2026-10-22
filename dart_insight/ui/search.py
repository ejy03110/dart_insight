"""Full-period metadata form and server-first bottom pagination."""
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import streamlit as st
from dart_insight.data.models import DISCLOSURE_TYPES
from dart_insight.services.disclosure_search import DisclosureSearchService,SearchQuery,SearchValidationError
from dart_insight.ui import routes


@st.cache_resource(show_spinner=False)
def search_service(settings):
    return DisclosureSearchService(settings)


def query_from_state():
    value=st.session_state.get('api_query')
    if not value:return None
    return SearchQuery(value['company'],date.fromisoformat(value['begin']),date.fromisoformat(value['end']),value['kind'],value['keyword'])


def turn_page(number):
    st.session_state['api_page']=number
    routes.write_route()


def pagination(page):
    if page.pages<1 or page.state!='success':return
    st.caption(f'총 {page.total:,}건 · {page.pages:,}페이지 · 현재 {page.page:,}페이지 · 페이지당 20건')
    start=max(1,min(page.page-2,page.pages-4));numbers=list(range(start,min(page.pages,start+4)+1))
    with st.container(horizontal=True,wrap=True,key='search_pagination'):
        st.button('≪',key='api_first',disabled=page.page<=1,on_click=turn_page,args=(1,),help='맨 처음')
        st.button('〈',key='api_previous',disabled=page.page<=1,on_click=turn_page,args=(page.page-1,),help='이전')
        for number in numbers:
            st.button(str(number),key=f'api_number_{number}',type='primary' if number==page.page else 'secondary',
                      disabled=number==page.page,on_click=turn_page,args=(number,))
        st.button('〉',key='api_next',disabled=page.page>=page.pages,on_click=turn_page,args=(page.page+1,),help='다음')
        st.button('≫',key='api_last',disabled=page.page>=page.pages,on_click=turn_page,args=(page.pages,),help='맨 마지막')


def render_search(settings,indexed,open_detail):
    today=datetime.now(ZoneInfo('Asia/Seoul')).date()
    previous=st.session_state.get('api_query',{})
    if previous:
        try:
            query_from_state()
        except (ValueError,KeyError,TypeError):
            st.error('URL의 검색 조건을 확인하고 다시 검색해 주세요.')
            st.session_state.pop('api_query',None)
            previous={}
    st.caption('기간을 지정해 전체 기업 또는 특정 기업의 공시 목록을 검색합니다. 본문과 AI 색인은 자동으로 생성하지 않습니다.')
    with st.form('api_search'):
        company=st.text_input('기업명·종목코드·기업코드 (선택)',value=previous.get('company',''),key='api_company',max_chars=100)
        columns=st.columns(2)
        with columns[0]:begin=st.date_input('시작일',value=date.fromisoformat(previous['begin']) if previous else today-timedelta(days=29),min_value=date(1999,1,1),max_value=today,key='api_begin')
        with columns[1]:end=st.date_input('종료일',value=date.fromisoformat(previous['end']) if previous else today,min_value=date(1999,1,1),max_value=today,key='api_end')
        kinds=['',*DISCLOSURE_TYPES]
        kind=st.selectbox('공시 유형',kinds,index=kinds.index(previous.get('kind','')) if previous.get('kind','') in kinds else 0,
                          format_func=lambda k:DISCLOSURE_TYPES.get(k,'전체 유형'),key='api_kind')
        keyword=st.text_input('공시명 검색어 (선택)',value=previous.get('keyword',''),key='api_keyword',max_chars=100)
        st.caption('전체 기업은 3개월 이내(최대 90일). 공시명 검색은 API 미지원으로 1,000건 이하 범위에서만 제공합니다.')
        submit=st.form_submit_button('공시 검색',key='api_submit')
    if submit:
        try:
            query=SearchQuery(company.strip(),begin,end,kind,keyword.strip())
            st.session_state['api_query']={'company':query.company,'begin':begin.isoformat(),'end':end.isoformat(),'kind':kind,'keyword':query.keyword}
            st.session_state['api_page']=1
            routes.write_route()
        except SearchValidationError as exc:
            st.error(str(exc));return
    try:query=query_from_state()
    except (ValueError,KeyError,TypeError):
        st.error('URL의 검색 조건을 확인하고 다시 검색해 주세요.');return
    if query is None:
        st.info('기업과 기간을 입력하고 공시 검색을 눌러 주세요.');return
    with st.spinner('공시를 찾고 있습니다.'):
        result=search_service(settings).search(query,st.session_state.get('api_page',1))
    if result.state=='error':
        st.error(result.message);return
    if result.state=='empty':
        st.info('조회 조건에 해당하는 공시가 없습니다.');return
    st.caption('동일 조건·페이지는 5분간 재사용합니다. 조회 실패 시 1분 후 다시 시도할 수 있습니다.')
    for note in result.notes:st.info(note)
    if query.keyword:st.caption(f'제목 검색 대상: API 목록 {result.api_total:,}건')
    for disclosure in result.items:
        with st.container(border=True):
            st.subheader(disclosure.report_nm)
            st.caption(f'{disclosure.corp_name} · {disclosure.rcept_dt} · {disclosure.disclosure_type}')
            known=indexed.get(disclosure.rcept_no)
            st.caption('AI 분석 가능 · 보관된 공시 · 기존 분석 연결' if known and known.corp_code==disclosure.corp_code else '미색인 공시 · 원문 확인 가능 · AI 분석 미지원')
            st.button('공시 상세 보기',key='api_detail_'+disclosure.rcept_no,on_click=open_detail,args=(disclosure,'api'))
            if known and known.corp_code==disclosure.corp_code:
                from dart_insight.ui.routes import write_route
                def open_analysis(receipt):
                    st.session_state['chosen_receipt']=receipt
                    st.session_state['ui_page']='공시 분석'
                    st.session_state['ui_section']=None
                    write_route()
                st.button('AI 분석 열기',key='api_analyze_'+disclosure.rcept_no,
                          on_click=open_analysis,args=(disclosure.rcept_no,))
    pagination(result)
