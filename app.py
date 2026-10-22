"""User screens over the existing RAG runtime; navigation never runs providers."""
from datetime import date
import logging
import streamlit as st

from dart_insight.config import ConfigurationError, DISCLAIMER, load_settings
from dart_insight.data.models import COMPANIES
from dart_insight.opendart.client import DartError
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
from dart_insight.services.discovery import discover, interpret_condition, empty_message
from dart_insight.services.runtime import Runtime, list_disclosures, open_store
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.query_extraction import QueryExtractionError
from dart_insight.ui.chat import history_for, save_turn, group_citations, separate_legacy_analysis, render_copy_option
from dart_insight.ui.layout import apply_theme, render_hero, render_company_overview, render_disclosure_header
from dart_insight.ui.diagnostics import render_diagnostics
from dart_insight.ui import admin_auth
from dart_insight.vectorstore.metadata import MetadataIndexError

operations = logging.getLogger("dart_insight.operations")
operations.setLevel(logging.INFO)
if not operations.handlers:
    operations.addHandler(logging.StreamHandler())
operations.propagate = False

st.set_page_config(page_title='DART Insight', page_icon='📄', layout='wide')
apply_theme()


def navigate(page, receipt=None, company=None):
    # Business selection is not a widget key: Streamlit cannot clean it up on page changes.
    st.session_state['ui_page'] = page
    if receipt is not None:
        st.session_state['chosen_receipt'] = receipt
    if company is not None:
        st.session_state['explore_company'] = company
        st.session_state['explore_search'] = ''
        st.session_state.pop('explore_dates', None)
        st.session_state['explore_type'] = '전체'
    if page in ('홈', '공시 탐색', '분석 기록'):
        st.session_state['ui_section'] = page
    elif page == '공시 분석':
        st.session_state['ui_section'] = None


def menu_changed():
    navigate(st.session_state['ui_section'])
    st.session_state['diagnostic_open'] = False


def remember_filter(name):
    st.session_state[name] = st.session_state['_' + name]
    st.session_state['explore_page'] = 1


histories = st.session_state.setdefault('histories', {})
analyses = st.session_state.setdefault('analyses', {})
separate_legacy_analysis(histories, analyses, ANALYSIS_QUESTION)
page = st.session_state.setdefault('ui_page', '홈')
st.session_state.setdefault('ui_section', '홈')
with st.sidebar:
    st.markdown('## DART Insight')
    st.caption('공시로 보는 기업의 오늘과 내일')
    st.radio('화면 이동', ['홈', '공시 탐색', '분석 기록'], index=None, key='ui_section',
             on_change=menu_changed, label_visibility='collapsed')
    st.divider()
    saved_ids = {r for r, turns in histories.items() if turns} | set(analyses)
    if saved_ids:
        st.caption('기업별 대화 기록')
        # Only real session records appear here, independent of exploration filters.
        for row in st.session_state.get('record_labels', []):
            if row['receipt'] in saved_ids:
                st.button(row['label'], key='history_' + row['receipt'],
                          on_click=navigate, args=('공시 분석', row['receipt']))
    if admin_auth.is_admin():
        st.toggle('진단 도구', key='diagnostic_open')
        st.button('관리자 로그아웃', key='admin_logout', on_click=admin_auth.logout)

try:
    secrets = dict(st.secrets) if st.secrets else {}
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    secrets = {}
try:
    settings = load_settings(secrets)
except ConfigurationError:
    st.error('서비스 설정을 확인할 수 없습니다. 관리자에게 문의해 주세요.')
    st.stop()

if st.query_params.get('admin') == 'login' and not admin_auth.is_admin():
    admin_auth.render_login()
    st.stop()
if st.session_state.get('diagnostic_open'):
    if admin_auth.is_admin():
        render_diagnostics(settings)
        st.stop()
    st.session_state['diagnostic_open'] = False


@st.cache_resource(show_spinner=False)
def cached_store(config):
    return open_store(config)


metadata, embeddings, disclosures = None, None, []
try:
    metadata, embeddings = cached_store(settings)
    disclosures = list_disclosures(metadata)
except Exception:
    # Technical DB errors and configuration belong to diagnostics, not the home screen.
    st.info('현재 공시를 불러올 수 없습니다. 잠시 후 다시 이용해 주세요.')
st.session_state['record_labels'] = [
    {'receipt': d.rcept_no, 'label': f'{d.corp_name} · {d.report_nm}'} for d in disclosures]
by_id = {d.rcept_no: d for d in disclosures}


if page == '홈':
    render_hero('홈')
    search = st.text_input('기업명 또는 종목코드 검색', placeholder='예: 삼성전자, 005930', key='home_search').strip()
    render_company_overview(disclosures, COMPANIES, search, navigate)
    st.caption(DISCLAIMER)
    st.stop()

if page == '분석 기록':
    st.title('분석 기록')
    st.caption('현재 접속에서 나눈 대화와 분석입니다. 접속이 종료되면 기록이 사라질 수 있습니다.')
    recorded = [d for d in disclosures if histories.get(d.rcept_no) or d.rcept_no in analyses]
    if not recorded:
        st.info('아직 분석 기록이 없습니다.')
    for disclosure in recorded:
        with st.container(border=True):
            st.subheader(disclosure.report_nm)
            st.caption(f'{disclosure.corp_name} · {disclosure.rcept_dt}')
            st.button('이어서 보기', key='record_' + disclosure.rcept_no,
                      on_click=navigate, args=('공시 분석', disclosure.rcept_no))
    st.stop()

if page == '공시 탐색':
    st.title('공시 탐색')
    st.caption('보관된 공시에서 찾습니다. 실시간 공시 조회 결과가 아닙니다.')
    for name, default in [('explore_search', ''), ('explore_company', '전체'), ('explore_type', '전체')]:
        st.session_state['_' + name] = st.session_state.get(name, default)
    search = st.text_input('기업명 또는 종목코드 검색', placeholder='예: 삼성전자, 005930',
                           key='_explore_search', on_change=remember_filter, args=('explore_search',)).strip()
    columns = st.columns(3)
    with columns[0]:
        company_filter = st.selectbox('기업', ['전체', *[c.name for c in COMPANIES]],
                                      key='_explore_company', on_change=remember_filter, args=('explore_company',))
    types = ['전체', *sorted({d.disclosure_type for d in disclosures})]
    if st.session_state['_explore_type'] not in types:
        st.session_state['_explore_type'] = '전체'
    with columns[1]:
        kind = st.selectbox('공시 유형', types, key='_explore_type', on_change=remember_filter, args=('explore_type',))
    dates = sorted(date.fromisoformat(d.rcept_dt) for d in disclosures if d.rcept_dt)
    selected_dates = None
    with columns[2]:
        if dates:
            st.session_state['_explore_dates'] = st.session_state.get('explore_dates', (dates[0], dates[-1]))
            selected_dates = st.date_input('공시 기간', min_value=dates[0], max_value=dates[-1],
                                           key='_explore_dates', on_change=remember_filter, args=('explore_dates',))
    options = [d for d in disclosures if (company_filter == '전체' or d.corp_name == company_filter)
               and (not search or search.casefold() in d.corp_name.casefold() or search in d.stock_code)
               and (kind == '전체' or d.disclosure_type == kind)]
    if selected_dates and len(selected_dates) == 2:
        options = [d for d in options if selected_dates[0].isoformat() <= d.rcept_dt <= selected_dates[1].isoformat()]
    elif selected_dates is not None:
        st.info('조회할 시작일과 종료일을 선택해 주세요.')
        options = []
    with st.expander('자연어 조건으로 공시 찾기'):
        with st.form('discovery'):
            condition = st.text_area('검색 조건', placeholder='2025년 삼성전자 사업보고서')
            find = st.form_submit_button('조건에 맞는 공시 검색')
        if find:
            if not settings.openai_api_key:
                st.error('AI 검색을 이용하려면 서비스 인증 설정이 필요합니다. 관리자에게 문의해 주세요.')
            elif not condition.strip():
                st.info('검색 조건을 입력해 주세요.')
            elif options:
                st.session_state['discovery_condition'] = condition
                try:
                    with st.status('조건 후보 검색·선별 중'):
                        llm = GmsClient(settings)
                        try:
                            code = options[0].corp_code if company_filter != '전체' else None
                            st.session_state['discovery_results'] = discover(metadata, condition, embeddings, settings, llm, code)
                        finally:
                            llm.close()
                except (ConfigurationError, DartError, LLMError, QueryExtractionError, MetadataIndexError) as exc:
                    st.session_state['discovery_results'] = []
                    st.error(str(exc))
                except Exception:
                    st.session_state['discovery_results'] = []
                    st.error('조건 검색 실패: 관리자에게 서비스 연결 확인을 요청해 주세요.')
        if 'discovery_results' in st.session_state:
            searched_condition = st.session_state.get('discovery_condition', '')
            st.caption(interpret_condition(searched_condition).description)
            option_ids = {d.rcept_no for d in options}
            matches = [r for r in st.session_state['discovery_results'] if r['metadata']['rcept_no'] in option_ids]
            if not matches:
                st.info(empty_message(searched_condition))
            for row in matches:
                m = row['metadata']
                st.button(f"{m['corp_name']} · {m['report_nm']} · {m['rcept_dt']}", key='result_' + m['rcept_no'],
                          on_click=navigate, args=('공시 분석', m['rcept_no']))
    if not options:
        st.info('조건에 맞는 보관된 공시가 없습니다.')
    else:
        # Keep large existing indexes usable without rendering thousands of widgets.
        pages = list(range(1, (len(options) - 1) // 20 + 2))
        if st.session_state.get('explore_page', 1) not in pages:
            st.session_state['explore_page'] = 1
        number = st.selectbox('목록 페이지', pages, key='explore_page')
        for disclosure in options[(number - 1) * 20:number * 20]:
            with st.container(border=True):
                st.subheader(disclosure.report_nm)
                st.caption(f'{disclosure.corp_name} · {disclosure.rcept_dt} · {disclosure.disclosure_type}')
                st.button('공시 분석 열기', key='open_' + disclosure.rcept_no,
                          on_click=navigate, args=('공시 분석', disclosure.rcept_no))
    st.stop()

receipt = st.session_state.get('chosen_receipt')
if receipt not in by_id:
    st.info('이 공시를 현재 목록에서 찾을 수 없습니다. 공시 탐색에서 다시 선택해 주세요.')
    st.stop()
selected = by_id[receipt]
st.button('← 공시 목록', key='back_to_explore', on_click=navigate, args=('공시 탐색',))
render_disclosure_header(selected)
st.caption(DISCLAIMER)


def render_result(result):
    st.markdown(result['content'])
    render_copy_option(result['content'])
    if result.get('audit', {}).get('query_fallback'):
        st.warning('검색어 추출에 실패하여 원래 질문으로 검색했습니다. 최종 답변은 기존 근거 검증을 통과한 결과입니다.')
    notes = result.get('audit', {}).get('parse_notes', [])
    if notes:
        st.warning('원문의 제목 표기 등 일부 형식을 보정했습니다. 자세한 내용은 원문 처리 상세에서 확인하세요.')
        with st.expander('원문 처리 상세'):
            for note in notes:
                st.text(note)
    if result.get('audit', {}).get('answer_basis') == 'conversation':
        st.caption('이전 대화에서 확인한 내용입니다. 새로운 공시 사실을 검증한 답변이 아닙니다.')
    elif not result.get('citations'):
        st.info('답변에 인용된 근거가 없습니다.')
    for group in group_citations(result.get('citations', [])):
        st.write(' · '.join(group['titles']))
        for citation in group['citations']:
            label = '항목 메타데이터' if citation['source_type'] == 'opendart_metadata' else '외부 문서'
            st.caption(f"[근거 {citation['number']}] {label}")
        st.write(group['source'])
        with st.expander('근거 내용 및 문서 식별 정보'):
            for citation in group['citations']:
                st.caption(f"[근거 {citation['number']}] 청크 {citation['id']}")
                st.text(citation.get('text', ''))
    if result.get('audit') and admin_auth.is_admin():
        with st.expander('분석 과정 상세'):
            st.json(result['audit'])


def render_turn(turn):
    with st.chat_message(turn['role']):
        if turn['role'] == 'assistant':
            render_result(turn)
        else:
            st.markdown(turn['content'])


def run_question(question, *, history=None):
    if not settings.openai_api_key:
        st.error('AI 분석을 이용하려면 서비스 인증 설정이 필요합니다. 관리자에게 문의해 주세요.')
        return None
    if not settings.dart_api_key and not settings.document_bundle:
        st.error('공시 원문에 연결할 수 없습니다. 관리자에게 연결 설정 확인을 요청해 주세요.')
        return None
    last_stage = 'start'
    try:
        with st.spinner('분석 중입니다. 잠시만 기다려 주세요.'):
            def progress(message):
                nonlocal last_stage
                # Stage details stay in the existing server logs, never in the UI.
                last_stage = next((phase for word, phase in (('XML', 'parse'), ('검색', 'retrieve'), ('원문', 'collect'), ('색인', 'index'), ('생성', 'generate')) if word in message), 'processing')
                operations.info('ui_phase=%s', last_stage)
            with Runtime(settings, metadata, embeddings) as service:
                result = service.ask(selected, question, progress, history=history) if history else service.ask(selected, question, progress)
        return result
    except (ConfigurationError, DartError, LLMError, QueryExtractionError, MetadataIndexError) as exc:
        operations.warning('ui_request_failed phase=%s exception=%s category=%s', last_stage, type(exc).__name__,
                           getattr(exc, 'category', 'processing'))
        st.error('분석을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요. 이전 결과와 대화는 유지됩니다.')
    except Exception as exc:
        # No raw exception, prompt, URL, or credential is copied into public output/logs.
        operations.error('ui_request_failed phase=%s exception=%s', last_stage, type(exc).__name__)
        st.error('처리 실패: 요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요. 이전 결과와 대화는 유지됩니다.')
    return None


with st.container(border=True, key='disclosure_analysis'):
    st.subheader('공시 핵심 분석')
    analysis = st.button('공시 핵심 분석 실행', key='run_analysis')
    if analysis:
        result = run_question(ANALYSIS_QUESTION)
        if result is not None:
            analyses[receipt] = {'content': result['answer'], 'citations': result['citations'],
                                 'audit': result.get('audit', {})}
            st.rerun()
    if receipt in analyses:
        render_result(analyses[receipt])
        st.caption('이 공시에 저장된 분석 결과입니다. 추가 질문으로 분석을 다시 실행하지 않습니다.')
    else:
        st.info('분석 실행 버튼을 누르면 핵심 내용 요약·문서에 명시된 위험 요인·추가 확인 필요 사항을 표시합니다.')

with st.container(key='disclosure_chat'):
    st.subheader('공시 관련 추가 질문')
    question = st.chat_input('선택 공시에 대해 추가 질문하세요', key='followup_question')
    for turn in history_for(histories, receipt):
        render_turn(turn)
    if question:
        result = run_question(question, history=[dict(turn) for turn in history_for(histories, receipt)])
        if result is not None:
            save_turn(histories, receipt, question, result)
            st.rerun()  # Refresh the saved conversation without rerunning analysis.
