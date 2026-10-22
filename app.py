"""Streamlit required workflow F2001-F2012. External calls require explicit UI actions."""
from datetime import date, timedelta

import streamlit as st

from dart_insight.config import ConfigurationError, DISCLAIMER, load_settings
from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError
from dart_insight.rag.prompts.answer import ANALYSIS_QUESTION
from dart_insight.services.discovery import discover, interpret_condition, empty_message
from dart_insight.services.runtime import Runtime, list_disclosures, open_store
from dart_insight.rag.llm import GmsClient, LLMError
from dart_insight.rag.query_extraction import QueryExtractionError
from dart_insight.ui.chat import history_for, save_turn, group_citations, separate_legacy_analysis, render_copy_option
from dart_insight.vectorstore.metadata import MetadataIndexError

st.set_page_config(page_title='DART Insight', page_icon='📄', layout='wide')
st.title('DART Insight')
st.caption('공시 선택 · 근거 기반 질의응답 · 공시 핵심 내용 및 위험 요인 분석')
st.warning(DISCLAIMER)
try:
    secrets = dict(st.secrets) if st.secrets else {}
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    secrets = {}
try:
    settings = load_settings(secrets)
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()

if not settings.dart_api_key and not settings.document_bundle:
    st.error('DART_API_KEY를 환경변수, .env 또는 Streamlit Secrets에 설정해 주세요. 실제 문서 묶음도 사용할 수 있습니다.')
if not settings.openai_api_key:
    st.info('질문·분석·조건 선별에는 GMS_API_KEY(기존 OPENAI_API_KEY 호환)를 설정해야 합니다. 시작만으로 API를 호출하지 않습니다.')

# Retain the verified F2001 connection check independently of the RAG store.
with st.expander('OpenDART 연결 확인 / 메타데이터 준비'):
    with st.container():
        company_name = st.selectbox('기업', [c.name for c in COMPANIES])
        begin = st.date_input('시작일', date.today() - timedelta(days=365))
        end = st.date_input('종료일', date.today())
        disclosure_type = st.selectbox('공시 유형', list(DISCLOSURE_TYPES),
                                       format_func=lambda key: DISCLOSURE_TYPES[key])
        refresh = st.checkbox('캐시 대신 실제 API로 연결 확인', value=True)
        submitted = st.button('OpenDART 연결 확인', key='connect_dart')
    if submitted:
        try:
            with st.status('공식 기업코드와 공시 목록을 조회하고 있습니다.') as status:
                company = next(c for c in COMPANIES if c.name == company_name)
                with DartClient(settings) as client:
                    corp = client.resolve_company(company, refresh=refresh)
                    items = client.list_disclosures(corp['corp_code'], begin, end,
                                                     disclosure_type=disclosure_type, max_pages=1, refresh=refresh)
                status.update(label='조회 완료', state='complete')
            if items:
                st.success(f'{len(items)}건을 조회했습니다. 연결 확인은 첫 페이지만 조회합니다.')
                st.dataframe([item.to_dict() for item in items], hide_index=True,
                             column_config={'source_url': st.column_config.LinkColumn('공시 원문')})
            else:
                st.info('해당 기간·유형에 조회된 공시가 없습니다.')
        except (ConfigurationError, DartError) as exc:
            st.error(str(exc))
        except Exception:
            st.error('연결 확인 실패: 네트워크·입력·캐시 경로를 확인해 주세요.')
    st.caption('일괄 수집 명령은 세 기업·전체 공시 유형 기준이며, 조회 기간은 위 선택값을 사용합니다.')
    st.code(f'python -m dart_insight.collect --begin {begin.isoformat()} --end {end.isoformat()}\n'
            'python -m dart_insight.index_metadata --input <실제 collection.json> '
            '--db-dir .local/data/chroma_bge_m3 --batch-size 16', language='bash')


@st.cache_resource(show_spinner=False)
def cached_store(config):
    return open_store(config)


try:
    metadata, embeddings = cached_store(settings)
    disclosures = list_disclosures(metadata)
except MetadataIndexError as exc:
    st.info(str(exc))
    st.stop()
except Exception:
    st.error('메타데이터 조회 실패: DB 경로·컬렉션 형식을 확인해 주세요.')
    st.stop()

st.sidebar.metric('메타데이터 색인 수', metadata.count())
st.sidebar.caption('항목 1건 = 개별 공시 1건. 모델은 첫 검색·색인 시 로드됩니다.')
company_filter = st.sidebar.selectbox('공시 기업', ['전체', *[c.name for c in COMPANIES]])
options = [d for d in disclosures if company_filter == '전체' or d.corp_name == company_filter]
if not options:
    st.info('선택 기업의 공시 색인이 없습니다.')
    st.stop()
by_id = {d.rcept_no: d for d in options}

with st.sidebar.form('discovery'):
    condition = st.text_area('자연어 조건으로 공시 찾기', placeholder='2025년 삼성전자 사업보고서')
    find = st.form_submit_button('조건에 맞는 공시 검색')
if find:
    if not settings.openai_api_key:
        st.error('GMS API 키를 OPENAI_API_KEY에 설정해 주세요.')
    elif not condition.strip():
        st.info('검색 조건을 입력해 주세요.')
    else:
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
            st.error('조건 검색 실패: GMS·모델 캐시·네트워크 설정을 확인해 주세요.')
if 'discovery_results' in st.session_state:
    matches = [r for r in st.session_state['discovery_results'] if r['metadata']['rcept_no'] in by_id]
    searched_condition = st.session_state.get('discovery_condition', '')
    st.sidebar.caption(interpret_condition(searched_condition).description)
    dates = sorted(d.rcept_dt for d in options if d.rcept_dt)
    if dates:
        st.sidebar.caption(f'현재 선택 범위의 색인 제출일: {dates[0]} ~ {dates[-1]}')
    if not matches:
        st.sidebar.info(empty_message(searched_condition))
    for row in matches:
        m = row['metadata']
        if st.sidebar.button(f"{m['corp_name']} · {m['report_nm']} · {m['rcept_dt']}", key='result_' + m['rcept_no']):
            st.session_state['chosen_receipt'] = m['rcept_no']
if st.session_state.get('chosen_receipt') not in by_id:
    st.session_state['chosen_receipt'] = options[0].rcept_no
receipt = st.selectbox('질문할 개별 공시', list(by_id), key='chosen_receipt',
                       format_func=lambda r: f'{by_id[r].corp_name} · {by_id[r].report_nm} · {by_id[r].rcept_dt} · {r}')
selected = by_id[receipt]
st.caption(f'공시 원문: {selected.source_url}')
histories = st.session_state.setdefault('histories', {})
analyses = st.session_state.setdefault('analyses', {})
separate_legacy_analysis(histories, analyses, ANALYSIS_QUESTION)


def render_result(result):
    st.markdown(result['content'])
    render_copy_option(result['content'])
    notes = result.get('audit', {}).get('parse_notes', [])
    if notes:
        st.warning('원문 XML의 제목 표기를 문자 보존 방식으로 보정했습니다. 아래 보정 내역을 확인하세요.')
        with st.expander('XML 보정 내역'):
            for note in notes:
                st.text(note)
    if not result.get('citations'):
        st.info('답변에 인용된 근거가 없습니다.')
    for group in group_citations(result.get('citations', [])):
        st.write(' · '.join(group['titles']))
        for citation in group['citations']:
            label = '항목 메타데이터' if citation['source_type'] == 'opendart_metadata' else '외부 문서'
            st.caption(f"[근거 {citation['number']}] {label}")
        st.write(group['source'])
        with st.expander('인용한 근거 청크 보기'):
            for citation in group['citations']:
                st.caption(f"[근거 {citation['number']}] 청크 {citation['id']}")
                st.text(citation.get('text', ''))
    if result.get('audit'):
        with st.expander('검색어 및 선별된 문서 확인'):
            st.json(result['audit'])


def render_turn(turn):
    with st.chat_message(turn['role']):
        if turn['role'] == 'assistant':
            render_result(turn)
        else:
            st.markdown(turn['content'])


def run_question(question):
    if not settings.openai_api_key:
        st.error('GMS_API_KEY(기존 OPENAI_API_KEY 호환)를 설정해 주세요.')
        return None
    if not settings.dart_api_key and not settings.document_bundle:
        st.error('DART_API_KEY 또는 실제 DOCUMENT_BUNDLE 설정이 필요합니다.')
        return None
    st.caption(f'선택 공시 1건과 관련 원문을 처리합니다. 임베딩 모델: {settings.embedding_model}, 장치: {settings.embedding_device}')
    try:
        with st.status('문서 수집 준비 중', expanded=True) as status:
            def progress(message):
                status.update(label=message)
                st.write(message)
            with Runtime(settings, metadata, embeddings) as service:
                result = service.ask(selected, question, progress)
            status.update(label='답변 및 근거 준비 완료', state='complete', expanded=False)
        return result
    except (ConfigurationError, DartError, LLMError, QueryExtractionError, MetadataIndexError) as exc:
        st.error(str(exc))
    except Exception:
        st.error('처리 실패: 키·외부 API·모델 캐시·원문·DB 설정을 확인해 주세요. 저장된 자료는 보존되며 같은 질문으로 재시도할 수 있습니다.')
    return None


with st.container(border=True, key='disclosure_analysis'):
    st.subheader('공시 핵심 분석')
    analysis = st.button('공시 핵심 분석 실행', key='run_analysis')
    if analysis:
        result = run_question(ANALYSIS_QUESTION)
        if result is not None:
            analyses[receipt] = {'content': result['answer'], 'citations': result['citations'],
                                 'audit': result.get('audit', {})}
    if receipt in analyses:
        render_result(analyses[receipt])
        st.caption('이 공시에 저장된 분석 결과입니다. 추가 질문으로 분석을 다시 실행하지 않습니다.')
    else:
        st.info('분석 실행 버튼을 누르면 핵심 내용 요약·문서에 명시된 위험 요인·추가 확인 필요 사항을 표시합니다.')

with st.container(key='disclosure_chat'):
    st.subheader('공시 관련 추가 질문')
    for turn in history_for(histories, receipt):
        render_turn(turn)
    question = st.chat_input('선택 공시에 대해 추가 질문하세요', key='followup_question')
    if question:
        result = run_question(question)
        if result is not None:
            save_turn(histories, receipt, question, result)
            for turn in history_for(histories, receipt)[-2:]:
                render_turn(turn)
