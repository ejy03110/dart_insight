"""Initial connection screen only; full F2009–F2012 UI is a later phase."""

from datetime import date, timedelta

import streamlit as st

from dart_insight.config import ConfigurationError, DISCLAIMER, load_settings
from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError

st.set_page_config(page_title="DART Insight", page_icon="📄")
st.title("DART Insight")
st.caption("초기 구성 · OpenDART API 연결 확인")
st.info("현재 단계에는 색인·질문 답변·요약 기능이 구현되어 있지 않습니다.")
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

if not settings.dart_api_key:
    st.error("DART_API_KEY를 환경변수, .env 또는 Streamlit Secrets에 설정해 주세요.")
    st.stop()

with st.form("connection"):
    company_name = st.selectbox("기업", [c.name for c in COMPANIES])
    begin = st.date_input("시작일", date.today() - timedelta(days=365))
    end = st.date_input("종료일", date.today())
    disclosure_type = st.selectbox("공시 유형", list(DISCLOSURE_TYPES),
                                   format_func=lambda key: DISCLOSURE_TYPES[key])
    refresh = st.checkbox("캐시 대신 실제 API로 연결 확인", value=True)
    submitted = st.form_submit_button("OpenDART 연결 확인")

if submitted:
    try:
        with st.status("공식 기업코드와 공시 목록을 조회하고 있습니다.") as status:
            company = next(c for c in COMPANIES if c.name == company_name)
            with DartClient(settings) as client:
                corp = client.resolve_company(company, refresh=refresh)
                items = client.list_disclosures(corp["corp_code"], begin, end,
                                               disclosure_type=disclosure_type,
                                               max_pages=1, refresh=refresh)
            status.update(label="조회 완료", state="complete")
        if items:
            st.success(f"{len(items)}건을 조회했습니다. 연결 확인은 첫 페이지만 조회합니다.")
            st.dataframe([item.to_dict() for item in items], hide_index=True,
                         column_config={"source_url": st.column_config.LinkColumn("공시 원문")})
        else:
            st.info("해당 기간·유형에 조회된 공시가 없습니다.")
    except (ConfigurationError, DartError) as exc:
        st.error(str(exc))
    except OSError:
        st.error("로컬 캐시 경로의 쓰기 권한과 디스크 공간을 확인해 주세요.")
