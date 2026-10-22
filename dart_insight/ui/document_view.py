"""Safe original reading with an explicit action; no RAG runtime dependency."""
from html import escape
import streamlit as st
from dart_insight.services.document_view import DocumentViewService


@st.cache_resource(show_spinner=False)
def document_service(settings):
    return DocumentViewService(settings)


def render_document(settings,disclosure):
    service=document_service(settings)
    result=service.peek(disclosure)
    label='공시 본문 불러오기' if result.state=='unloaded' else '본문 새로고침'
    if st.button(label,key='load_document'):
        with st.spinner('본문을 불러오고 있습니다.'):
            result=service.fetch(disclosure)
    if result.state=='success':
        for note in result.notes:
            st.caption(note)
        # Only the service-generated whitelist markup is trusted; source markup is never injected.
        content=result.html or '<div class="document-text">'+escape(result.text)+'</div>'
        st.html('<article class="original-document">'+content+'</article>')
    else:
        messages={'unloaded':'본문 불러오기를 누르면 실제 공시 내용을 확인할 수 있습니다.',
                  'empty':'제공된 본문이 비어 있습니다. 공식 DART 원문에서 확인해 주세요.',
                  'unsupported':'이 공시는 앱에서 본문을 표시할 수 없는 형식입니다. 공식 DART 원문에서 확인해 주세요.',
                  'error':'본문을 불러오지 못했습니다. 잠시 후 다시 시도하거나 공식 DART 원문을 확인해 주세요.'}
        st.info(messages[result.state])
    st.caption('본문 열람은 AI 분석과 별개입니다. 조회 결과는 1시간 재사용하며 실패 시 1분 후 재시도할 수 있습니다.')
