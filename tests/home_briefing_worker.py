"""Home UI -> real parser/index/graph, with isolated DB and fake providers only.

Run in a child so all Chroma native handles close before Windows temp cleanup.
"""
from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
import streamlit as st
from streamlit.testing.v1 import AppTest
from test_rag_ui import FIRST, APP
from rag_integration_worker import Encoder, LLM
from ux05_integration_worker import zipped
from dart_insight.config import Settings
from dart_insight.data.models import COMPANIES
from dart_insight.services.original_documents import OpenDartSource
from dart_insight.services.recent_disclosures import RecentResult
from dart_insight.vectorstore.documents import index_documents


class TrackingLLM(LLM):
    def __init__(self):
        super().__init__()
        self.answer_calls=0

    def json(self, system, payload, validate):
        if 'evidence' in payload:
            self.answer_calls+=1
        return super().json(system,payload,validate)

    def close(self):
        pass


def main(root):
    check=unittest.TestCase()
    for missing in (False,True):
        st.cache_resource.clear()
        folder=root/('missing' if missing else 'success')
        settings=Settings(data_dir=folder,db_dir=folder/'chroma',dart_api_key='test-only',openai_api_key='test-only')
        client=Mock();client.list_disclosures.return_value=[]
        content=(b'<result><status>014</status><message>SECRET</message></result>' if missing else
                 zipped('<DOCUMENT><P>사업 위험: 환율 변동 위험이 있습니다.</P></DOCUMENT>'.encode()))
        client._request.return_value=Mock(content=content)
        source=OpenDartSource(settings,client);llm=TrackingLLM()
        recent={company.stock_code:RecentResult(company,date(2025,1,1),date(2025,1,7),
                'success' if i==0 else 'empty',(FIRST,) if i==0 else ())
                for i,company in enumerate(COMPANIES)}
        with patch('dart_insight.ui.routes.register_pages',return_value=None), \
             patch('dart_insight.config.load_settings',return_value=settings), \
             patch('dart_insight.ui.recent.recent_controls',return_value=recent), \
             patch('dart_insight.services.runtime.create_embeddings',return_value=Encoder()), \
             patch('dart_insight.services.runtime.GmsClient',return_value=llm), \
             patch('dart_insight.services.runtime.OpenDartSource',return_value=source), \
             patch('dart_insight.services.rag_service.index_documents',wraps=index_documents) as indexed, \
             patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected network')):
            app=AppTest.from_file(APP,default_timeout=30).run()
            check.assertEqual(len(app.exception),0)
            check.assertEqual(client._request.call_count,1)
            if missing:
                check.assertNotIn(FIRST.rcept_no,app.session_state['analyses'])
                check.assertEqual(app.session_state['analysis_failures'][FIRST.rcept_no]['reason'],'original_unavailable')
                check.assertEqual(llm.answer_calls,0)
                check.assertEqual(indexed.call_count,0)
            else:
                result=app.session_state['analyses'][FIRST.rcept_no]
                check.assertTrue(any(c['source_type']=='opendart_document' for c in result['citations']))
                check.assertEqual(llm.answer_calls,1)
                check.assertEqual(indexed.call_count,1)
            calls=(llm.extract_calls,llm.select_calls,llm.answer_calls,indexed.call_count)
            app.run();app.run()
            check.assertEqual(client._request.call_count,1)
            check.assertEqual(calls,(llm.extract_calls,llm.select_calls,llm.answer_calls,indexed.call_count))
    print('HOME: actual UI/parser/Chroma/SQLite/LangGraph success + original missing + no duplicate calls PASS')


if __name__=='__main__':
    main(Path(sys.argv[1]))
