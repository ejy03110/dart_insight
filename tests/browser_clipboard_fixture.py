"""Browser-only UI fixture: no real API/data/model, using the production copy UI."""
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import streamlit as st
from dart_insight.ui.chat import render_copy_option

st.set_page_config(page_title='DART clipboard regression fixture')


@st.cache_resource
def cache_marker():
    return uuid4().hex


st.markdown('분석 결과 복사 테스트')
st.caption('CACHE_MARKER:' + cache_marker())
render_copy_option('분석 결과 복사 테스트\n- 테스트 전용 요약 [근거 1]')
with st.chat_message('assistant'):
    st.markdown('채팅 답변 복사 테스트')
    render_copy_option('채팅 답변 복사 테스트\n테스트 전용 답변 [근거 2]')
st.button('캐시 유지 확인', key='rerun_check')
