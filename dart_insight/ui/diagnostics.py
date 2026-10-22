"""Opt-in diagnostic screen; no requests until the connection button is pressed."""
from datetime import date, timedelta
import streamlit as st
from dart_insight.config import ConfigurationError
from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES


def render_diagnostics(settings):
    from dart_insight.opendart.client import DartClient, DartError
    st.title("진단 도구")
    st.caption("운영 설정과 연결을 확인하는 별도 화면입니다.")
    from dart_insight.services.runtime import open_store
    try:
        metadata, _ = open_store(settings)
        st.metric('메타데이터 색인 수', metadata.count())
        st.caption(f'임베딩 모델: {settings.embedding_model}, 장치: {settings.embedding_device}')
    except Exception as exc:
        from dart_insight.vectorstore.metadata import MetadataIndexError
        st.info(str(exc) if isinstance(exc, MetadataIndexError) else 'DB 경로·컬렉션 형식을 확인해 주세요.')
    if not settings.dart_api_key and not settings.document_bundle:
        st.error("DART_API_KEY를 환경변수, .env 또는 Streamlit Secrets에 설정해 주세요.")
    if not settings.openai_api_key:
        st.info("GMS_API_KEY(기존 OPENAI_API_KEY 호환)를 설정해야 AI 분석을 사용할 수 있습니다.")
    # Retain the verified F2001 connection check independently of the RAG store.
    with st.expander('OpenDART 연결 확인 / 메타데이터 준비', expanded=True):
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
