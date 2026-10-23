"""Unindexed API click: real UI, Runtime, ZIP parser, Chroma, LangGraph."""
from pathlib import Path
import sys
from unittest.mock import Mock, patch
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from streamlit.testing.v1 import AppTest
import streamlit as st
from test_rag_ui import FIRST, APP
from rag_integration_worker import Encoder, LLM
from ux05_integration_worker import zipped
from dart_insight.config import Settings
from dart_insight.services.disclosure_search import SearchPage
from dart_insight.services.original_documents import OpenDartSource


class ContextLLM(LLM):
    def extract(self,selected,question,*,history=None):return super().extract(selected,question)
    def close(self):pass


def main(root):
    check=unittest.TestCase()
    st.cache_resource.clear()
    settings=Settings(data_dir=root,db_dir=root/'chroma',dart_api_key='test-only',openai_api_key='test-only')
    client=Mock();client.list_disclosures.return_value=[]
    client._request.return_value=Mock(content=zipped('<DOCUMENT><P>사업 위험: 환율 변동 위험이 있습니다.</P></DOCUMENT>'.encode()))
    source=OpenDartSource(settings,client)
    search=Mock();search.search.return_value=SearchPage('success',(FIRST,),1,1,1,1)
    with patch('dart_insight.ui.routes.register_pages',return_value=None),patch('dart_insight.config.load_settings',return_value=settings),patch('dart_insight.services.runtime.create_embeddings',return_value=Encoder()),patch('dart_insight.services.runtime.GmsClient',side_effect=lambda s:ContextLLM()),patch('dart_insight.services.runtime.OpenDartSource',return_value=source),patch('dart_insight.ui.search.search_service',return_value=search),patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected network')):
        app=AppTest.from_file(APP,default_timeout=30)
        app.query_params.update(v='explore',scope='all',c='005930',begin='2025-01-01',end='2025-12-31')
        app.run()
        check.assertEqual(client._request.call_count,0)
        check.assertFalse((root/'chroma'/'chroma.sqlite3').exists())
        app.button(key='api_detail_'+FIRST.rcept_no).click().run()
        check.assertEqual(len(app.exception),0)
        check.assertIn(FIRST.rcept_no,app.session_state['analyses'])
        check.assertEqual(client._request.call_count,1)
        check.assertTrue((root/'chroma'/'chroma.sqlite3').is_file())
        check.assertFalse(app.chat_input[0].proto.disabled)
        app.run()
        check.assertEqual(client._request.call_count,1)
        app.chat_input[0].set_value('그럼 위험을 쉽게 설명해줘').run()
        check.assertEqual(len(app.exception),0)
        check.assertEqual(len(app.chat_message),2)
        check.assertEqual(client._request.call_count,1)
        print('UX05 UI: API click with no DB to automatic real parsing/indexing/graph and contextual followup PASS')

if __name__=='__main__':main(Path(sys.argv[1]))
