"""User screens over the existing RAG runtime; automatic analysis reuses receipt-scoped results."""
import logging
import streamlit as st

from dart_insight.config import ConfigurationError, DISCLAIMER, load_settings
from dart_insight.data.models import COMPANIES
from dart_insight.opendart.client import DartError
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
from dart_insight.services.runtime import Runtime, list_disclosures, open_store, analysis_readiness
from dart_insight.rag.llm import LLMError
from dart_insight.rag.query_extraction import QueryExtractionError
from dart_insight.ui.chat import history_for, save_turn, group_citations, separate_legacy_analysis, render_copy_option
from dart_insight.ui.layout import apply_theme, render_hero, render_disclosure_header
from dart_insight.ui.diagnostics import render_diagnostics
from dart_insight.ui import admin_auth, routes
from dart_insight.ui.recent import recent_controls
from dart_insight.vectorstore.metadata import MetadataIndexError

operations = logging.getLogger("dart_insight.operations")
operations.setLevel(logging.INFO)
if not operations.handlers:
    operations.addHandler(logging.StreamHandler())
operations.propagate = False

st.set_page_config(page_title='DART Insight', page_icon='📄', layout='wide')

selected_route = routes.register_pages()
apply_theme()
routes.sync_route(selected_route)


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
        st.session_state['explore_scope'] = '전체 기간 검색'
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('Asia/Seoul')).date()
        st.session_state['api_query'] = {'company': company, 'begin': (today-timedelta(days=29)).isoformat(), 'end': today.isoformat(), 'kind': '', 'keyword': ''}
        st.session_state['api_page'] = 1
    if page in ('홈', '공시 탐색', '분석 기록'):
        st.session_state['ui_section'] = page
    elif page in ('공시 분석', '공시 상세'):
        st.session_state['ui_section'] = None
    routes.write_route()


RETURN_FILTERS = ('explore_search','explore_company','explore_type','explore_dates','explore_page',
                  'recent_search','recent_company','recent_type','recent_dates','recent_page','api_query','api_page','stored_page')


def open_recent(disclosure,origin='recent'):
    st.session_state['recent_return'] = {
        'scope': '전체 기간 검색',
        'filters': {key:st.session_state[key] for key in RETURN_FILTERS if key in st.session_state}}
    st.session_state['recent_detail'] = disclosure
    st.session_state.setdefault('session_disclosures', {})[disclosure.rcept_no] = disclosure
    st.session_state['chosen_receipt'] = disclosure.rcept_no
    st.session_state['detail_origin'] = origin
    navigate('공시 상세')


def back_to_recent():
    snapshot=st.session_state.get('recent_return',{})
    st.session_state.update(snapshot.get('filters',{}))
    st.session_state['explore_scope'] = snapshot.get('scope','최근 7일 공시')
    navigate('공시 탐색')


def menu_changed():
    navigate(st.session_state['ui_section'])
    st.session_state['diagnostic_open'] = False


histories = st.session_state.setdefault('histories', {})
analyses = st.session_state.setdefault('analyses', {})
separate_legacy_analysis(histories, analyses, ANALYSIS_QUESTION)
page = st.session_state.setdefault('ui_page', '홈')
st.session_state.setdefault('ui_section', '홈')
if page not in ('공시 상세', '공시 분석'):
    st.session_state.pop('_detail_scroll_receipt', None)
st.session_state.setdefault('explore_scope','전체 기간 검색')
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

if not settings.dart_api_key and not settings.document_bundle:
    st.info('공시 조회 연결 설정이 없습니다. 관리자에게 API 인증 설정을 요청해 주세요. 저장된 공시는 계속 탐색할 수 있습니다.')
if not settings.openai_api_key:
    st.info('AI 서비스 인증 설정이 없어 분석을 시작할 수 없습니다. 원문과 공시 목록은 별도로 확인할 수 있습니다.')

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
except MetadataIndexError:
    st.info('저장된 AI 메타데이터 색인이 없습니다. 공시를 선택하면 필요한 자료를 자동 준비합니다. 공식 API 공시 검색은 계속 이용할 수 있습니다.')
except Exception:
    # Technical DB errors and configuration belong to diagnostics, not the home screen.
    st.info('보관된 공시를 불러올 수 없습니다. 잠시 후 다시 이용해 주세요.')
st.session_state['record_labels'] = [
    {'receipt': d.rcept_no, 'label': f'{d.corp_name} · {d.report_nm}'} for d in disclosures]
by_id = dict(st.session_state.get('session_disclosures', {}))
by_id.update({d.rcept_no: d for d in disclosures})
st.session_state['record_labels'] = [{'receipt': d.rcept_no, 'label': f'{d.corp_name} · {d.report_nm}'} for d in by_id.values()]
availability_cache = {}


def render_result(result, *, company=None):
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
            st.caption(f"[근거 {citation['number']}] {citation.get('title') or '문서명 미제공'} · {label}")
        st.write(group['source'])
        with st.expander('근거 내용 및 문서 식별 정보'):
            for citation in group['citations']:
                from dart_insight.ui.evidence import public_excerpt, filing_date
                st.caption(f"[근거 {citation['number']}] {citation.get('title') or '문서명 미제공'} · {filing_date(citation['source'])} · {company or '기업명 미제공'} · 인용 구간 {citation['number']}")
                st.text(public_excerpt(citation.get('text', '')))
            st.caption('표 구조가 남아 있지 않은 근거는 행·열을 추정하지 않습니다. 원문 링크에서 표를 확인하세요. 개인정보 일부는 표시만 생략하며 저장된 근거와 인용 번호는 유지합니다.')
    if result.get('audit') and admin_auth.is_admin():
        with st.expander('분석 과정 상세'):
            st.json(result['audit'])


def render_turn(turn, *, company=None):
    with st.chat_message(turn['role']):
        if turn['role'] == 'assistant':
            render_result(turn, company=company)
        else:
            st.markdown(turn['content'])


def run_question(question, *, history=None, target):
    if not settings.openai_api_key:
        st.error('AI 분석을 이용하려면 서비스 인증 설정이 필요합니다. 관리자에게 문의해 주세요.')
        return None
    if not settings.dart_api_key and not settings.document_bundle:
        st.error('공시 원문에 연결할 수 없습니다. 관리자에게 연결 설정 확인을 요청해 주세요.')
        return None
    last_stage = 'start'
    status = st.status('분석을 준비하고 있습니다.', expanded=True)
    stage_text = status.empty()
    seen_labels = []
    current_label = '분석 준비'
    try:
        with st.spinner('분석 중입니다. 잠시만 기다려 주세요.'):
            def progress(message):
                nonlocal last_stage, current_label
                # Only known callback events become public text; never echo raw errors.
                last_stage = next((phase for word, phase in (('XML', 'parse'), ('검색', 'retrieve'), ('원문', 'collect'), ('색인', 'index'), ('생성', 'generate')) if word in message), 'processing')
                operations.info('ui_phase=%s', last_stage)
                labels = {
                    '기존 본문 색인 확인 중': '저장된 공시 본문을 확인하고 있습니다.',
                    '기존 본문 색인 재사용 중': '준비된 원문을 재사용하고 있습니다.',
                    '선택 공시 메타데이터 색인 중': '선택 공시를 분석 목록에 준비하고 있습니다.',
                    '외부 문서 검색어 생성 중': '질문에 맞는 검색 조건을 준비하고 있습니다.',
                    '외부 출처 및 메타데이터에서 문서 후보 검색 중': '관련 공시를 찾고 있습니다.',
                    'LLM으로 관련 후보 문서 선별 중': '질문과 관련된 문서를 확인하고 있습니다.',
                    '원문 다운로드 중': '공시 원문을 수집하고 있습니다.',
                    '선별 공시 원문 수집 중': '공시 원문을 수집하고 있습니다.',
                    'XML 처리 중': '공시 원문을 읽고 있습니다.',
                    '임베딩 및 색인 중': '저장된 문서 색인을 확인하고 필요한 문서를 준비하고 있습니다.',
                    '관련 근거 검색 중': '공시에서 답변 근거를 찾고 있습니다.',
                    'GMS 답변 생성 중': '근거를 바탕으로 답변을 작성하고 있습니다.',
                    '근거 기반 답변 생성 중': '근거를 바탕으로 답변을 작성하고 있습니다.',
                    '이전 대화 확인 완료': '이전 대화에서 답변을 확인했습니다.',
                }
                label = labels.get(message)
                if message.startswith('검색어 추출 실패로'):
                    label = '검색 조건을 만들지 못해 원래 질문으로 검색을 계속합니다.'
                if label:
                    current_label = label
                    if label not in seen_labels:
                        seen_labels.append(label)
                    status.update(label=label)
                    stage_text.markdown('\n\n'.join(('진행 중 · ' if item == label else '완료 · ') + item for item in seen_labels))
            with Runtime(settings, metadata, embeddings) as service:
                result = service.ask(target, question, progress, history=history) if history else service.ask(target, question, progress)
        stage_text.markdown('\n\n'.join('완료 · ' + item for item in seen_labels))
        status.update(label='분석이 완료되었습니다.', state='complete', expanded=False)
        return result
    except (ConfigurationError, DartError, LLMError, QueryExtractionError, MetadataIndexError) as exc:
        operations.warning('ui_request_failed phase=%s exception=%s category=%s', last_stage, type(exc).__name__,
                           getattr(exc, 'category', 'processing'))
        category = getattr(exc, 'category', None)
        code = getattr(exc, 'code', '')
        if isinstance(exc, ConfigurationError) or category == 'authentication' or code in ('010', '011', '012'):
            hint = '서비스 인증 설정을 확인해 주세요.'
        elif code == '014' or isinstance(code, str) and code.startswith('document'):
            hint = '원문을 정상적으로 읽지 못해 불완전한 본문으로 분석하지 않았습니다.'
        elif category == 'rate_limit' or code == '020':
            hint = '호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.'
        elif category in ('schema', 'citation', 'grounding', 'truncated', 'empty_content', 'json_syntax'):
            hint = 'AI 응답의 형식 또는 근거 검증에 실패해 답변을 표시하지 않았습니다.'
        elif isinstance(exc, MetadataIndexError):
            hint = '색인 또는 모델 설정을 확인해 주세요. 기존 자료는 보존됩니다.'
        else:
            hint = '외부 서비스 연결과 호출 한도를 확인한 뒤 다시 시도해 주세요.'
        st.error('분석을 완료하지 못했습니다. ' + hint + ' 이전 결과와 대화는 유지됩니다.')
    except Exception as exc:
        # No raw exception, prompt, URL, or credential is copied into public output/logs.
        operations.error('ui_request_failed phase=%s exception=%s', last_stage, type(exc).__name__)
        st.error('처리 실패: 요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요. 이전 결과와 대화는 유지됩니다.')
    stage_text.markdown('\n\n'.join(('실패 · ' if item == current_label else '완료 · ') + item for item in seen_labels))
    status.update(label='실패 단계: ' + current_label, state='error', expanded=True)
    return None


def availability(disclosure):
    if not settings.openai_api_key:
        return False, 'AI 서비스 인증 설정이 없어 분석을 시작할 수 없습니다.'
    if not settings.dart_api_key and not settings.document_bundle:
        return False, '공시 원문 연결 설정이 없어 분석을 시작할 수 없습니다.'
    key = (disclosure.rcept_no, disclosure.corp_code)
    if key not in availability_cache:
        known = by_id.get(disclosure.rcept_no)
        ready, reason = analysis_readiness(settings, disclosure) if known and known.corp_code == disclosure.corp_code else (False, '')
        availability_cache[key] = (True, 'AI 분석 가능 · 본문 재사용' if ready else '미색인 공시 · 원문 자동 준비 후 AI 분석')
    return availability_cache[key]


def render_automatic(disclosure):
    from dart_insight.ui.analysis import ensure_analysis
    receipt = disclosure.rcept_no
    attempts = st.session_state.setdefault('analysis_attempts', {})
    ready, reason = availability(disclosure)
    if receipt not in analyses and not ready:
        st.info(reason)
        return
    retry = False
    had_attempt = attempts.get(receipt) in ('failed', 'running')
    if receipt not in analyses and attempts.get(receipt) in ('failed', 'running'):
        st.warning('이 공시의 분석이 완료되지 않았습니다. 자동 재호출하지 않습니다.')
        retry = st.button('분석 다시 시도', key='retry_analysis_' + receipt)
    result = ensure_analysis(receipt, analyses, attempts,
                             lambda: run_question(ANALYSIS_QUESTION, target=disclosure), retry=retry)
    if result is not None:
        render_result(result, company=disclosure.corp_name)
        st.caption('현재 세션에 저장된 분석입니다. 화면 전환·추가 질문으로 같은 분석을 반복하지 않습니다.')
    elif attempts.get(receipt) == 'failed' and not had_attempt:
        st.button('분석 다시 시도', key='retry_analysis_' + receipt, on_click=lambda: attempts.pop(receipt, None))


def render_analysis_view(disclosure):
    with st.container(border=True, key='disclosure_analysis'):
        st.subheader('공시 핵심 분석')
        render_automatic(disclosure)
    with st.container(key='disclosure_chat'):
        st.subheader('공시 관련 추가 질문')
        receipt = disclosure.rcept_no
        for turn in history_for(histories, receipt):
            render_turn(turn, company=disclosure.corp_name)
        # Auto analysis must complete before follow-up generation can begin.
        if not settings.openai_api_key or not (settings.dart_api_key or settings.document_bundle):
            st.info('질문을 보내려면 AI 및 공시 원문 연결 설정이 필요합니다.')
            return
        question = st.chat_input('선택 공시에 대해 추가 질문하세요', key='followup_question', disabled=receipt not in analyses)
        if question:
            from dart_insight.ui.analysis import followup_history
            result = run_question(question, target=disclosure, history=followup_history(receipt, analyses, histories))
            if result is not None:
                save_turn(histories, receipt, question, result)
                st.rerun()


if page == '홈':
    render_hero('홈')
    search = st.text_input('기업명 또는 종목코드 검색', placeholder='예: 삼성전자, 005930', key='home_search').strip()
    recent = recent_controls(settings, auto=True)
    from dart_insight.ui.dashboard import render_carousel
    render_carousel(COMPANIES, search, recent, open_recent, navigate, availability)
    st.subheader('오늘의 AI 브리핑')
    st.caption('최근 7일 공시 중 최신 대표 1건만 세션당 원문 준비와 분석을 자동 실행합니다. 기업 카드 이동으로 분석 대상을 바꾸거나 추가 호출하지 않습니다.')
    if 'briefing_receipt' not in st.session_state:
        candidates = sorted((d for result in recent.values() for d in result.disclosures), key=lambda d:(d.rcept_dt,d.rcept_no), reverse=True)
        representative = next((d for d in candidates if availability(d)[0]), None)
        if representative:
            st.session_state['briefing_receipt'] = representative.rcept_no
            st.session_state.setdefault('session_disclosures', {})[representative.rcept_no] = representative
            by_id[representative.rcept_no] = representative
    representative = by_id.get(st.session_state.get('briefing_receipt'))
    if representative:
        st.caption(f'{representative.corp_name} · {representative.report_nm} · 공시 접수일 {representative.rcept_dt}')
        render_automatic(representative)
        if st.session_state.get('analysis_attempts', {}).get(representative.rcept_no) == 'failed':
            st.info('대표 공시의 준비 또는 분석에 실패했습니다. 다른 공시를 자동으로 추가 분석하지 않습니다. 위 재시도 버튼이나 기업 카드의 다른 공시를 이용해 주세요.')
    else:
        st.info('최근 공시가 없거나 서비스 연결 설정이 없어 자동 브리핑을 실행하지 않았습니다. 공시 탐색에서 기존 공시와 원문을 확인하세요.')
    st.caption(DISCLAIMER)
    st.stop()

if page == '분석 기록':
    st.title('분석 기록')
    st.caption('현재 세션의 기록입니다. 브라우저 새로고침이나 세션 종료 후에는 복원되지 않을 수 있습니다.')
    recorded = [d for d in by_id.values() if histories.get(d.rcept_no) or d.rcept_no in analyses]
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
    # Legacy scope URLs are accepted, but never require a source choice.
    st.session_state['explore_scope'] = '전체 기간 검색'
    from dart_insight.ui.explore import render_unified
    render_unified(settings, by_id, metadata, embeddings, open_recent, availability)
    st.stop()

if page == '공시 상세':
    recent_selected = st.session_state.get('recent_detail')
    if recent_selected is None and st.session_state.get('route_receipt'):
        receipt=st.session_state['route_receipt']
        if receipt in by_id:
            recent_selected = by_id[receipt]
        elif st.session_state.get('detail_origin')=='api':
            from dart_insight.ui.search import query_from_state,search_service
            try:
                query=query_from_state()
                if query:
                    with st.spinner('공시 정보를 확인하고 있습니다.'):
                        result=search_service(settings).search(query,st.session_state.get('api_page',1))
                    recent_selected=next((d for d in result.items if d.rcept_no==receipt),None)
            except (ValueError,KeyError,TypeError):
                pass
        else:
            from dart_insight.ui.recent import recent_service
            with st.spinner('최근 공시 정보를 확인하고 있습니다.'):
                recent_results = recent_service(settings).refresh()
            recent_selected=next((d for r in recent_results.values() for d in r.disclosures if d.rcept_no==receipt),None)
        if recent_selected is not None:
            st.session_state['recent_detail']=recent_selected
    if recent_selected is None:
        st.info('이 주소의 공시 정보를 현재 조회 범위에서 복원하지 못했습니다. 공시 탐색에서 다시 선택해 주세요.')
        st.button('공시 탐색으로 이동',on_click=back_to_recent)
        st.stop()
    from dart_insight.ui.layout import reset_detail_scroll
    reset_detail_scroll(recent_selected.rcept_no)
    render_disclosure_header(recent_selected,show_source=False,show_identifiers=False)
    st.caption(DISCLAIMER)
    st.session_state.setdefault('session_disclosures', {})[recent_selected.rcept_no] = recent_selected
    by_id.setdefault(recent_selected.rcept_no, recent_selected)
    render_analysis_view(recent_selected)
    with st.expander('공시 원문 전체 및 표 확인', key='original_view_' + recent_selected.rcept_no):
        from dart_insight.ui.document_view import render_document
        render_document(settings, recent_selected)
    st.link_button('공식 DART 원문 보기 ↗',recent_selected.source_url)
    with st.container(key='detail_return'):
        st.button('← 공시 목록으로 돌아가기', key='back_to_recent', on_click=back_to_recent)
    st.stop()

receipt = st.session_state.get('chosen_receipt')
if receipt not in by_id:
    st.info('이 공시를 현재 목록에서 찾을 수 없습니다. 공시 탐색에서 다시 선택해 주세요.')
    st.stop()
selected = by_id[receipt]
st.button('← 공시 목록', key='back_to_explore', on_click=navigate, args=('공시 탐색',))
from dart_insight.ui.layout import reset_detail_scroll
reset_detail_scroll(selected.rcept_no)
render_disclosure_header(selected)
st.caption(DISCLAIMER)


render_analysis_view(selected)
