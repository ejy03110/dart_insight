"""Synthetic browser fixture: blocks external HTTP and RAG execution."""
import sys,runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from unittest.mock import MagicMock,Mock,patch
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import streamlit as st
from dart_insight.config import Settings
from dart_insight.services.disclosure_search import DisclosureSearchService
from test_disclosure_search import api_payload
from test_rag_ui import FIRST

@st.cache_resource
def service():
 client=MagicMock();client.__enter__.return_value=client
 client._json.side_effect=lambda endpoint,params:api_payload(params)
 client.corporate_codes.return_value=[{'corp_code':'00126380','corp_name':'삼성전자','stock_code':'005930'}]
 return DisclosureSearchService(Settings(dart_api_key='test-only'),client_factory=lambda s:client),client
search,client=service()
with patch('dart_insight.config.load_settings',return_value=Settings(dart_api_key='test-only')),patch('dart_insight.services.runtime.open_store',return_value=(Mock(count=lambda:1),Mock())),patch('dart_insight.services.runtime.list_disclosures',return_value=[FIRST]),patch('dart_insight.ui.search.search_service',return_value=search),patch('dart_insight.services.runtime.Runtime',side_effect=AssertionError('No RAG on navigation')),patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected network')):
 st.caption('SEARCH_CALLS:'+str(client._json.call_count))
 runpy.run_path(str(ROOT/'app.py'),run_name='__main__')
