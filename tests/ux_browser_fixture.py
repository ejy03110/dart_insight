"""Synthetic-only UX browser harness; all provider/HTTP/model boundaries blocked."""
import runpy
import sys
from pathlib import Path
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock, MagicMock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import streamlit as st
from dart_insight.config import Settings
from dart_insight.data.models import COMPANIES
from dart_insight.services.recent_disclosures import RecentDisclosureService
from dart_insight.services.document_view import DocumentViewService
from dart_insight.services.disclosure_search import DisclosureSearchService
from test_rag_ui import FIRST
from test_disclosure_search import api_payload

@st.cache_resource(show_spinner=False)
def boundaries():
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    rows=[replace(FIRST,rcept_no=now.strftime('%Y%m%d')+f'{i:06d}',rcept_dt=now.date().isoformat(),report_nm='브라우저 검증용 보유현황 '+str(i),source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+now.strftime('%Y%m%d')+f'{i:06d}') for i in (1,2,3)]
    settings=Settings(dart_api_key='test-only',openai_api_key='test-only')
    recent_client=MagicMock();recent_client.__enter__.return_value=recent_client
    recent_client.resolve_company.side_effect=lambda c:{'corp_code':FIRST.corp_code if c==COMPANIES[0] else '99999999'}
    recent_client.list_disclosures.side_effect=lambda code,*a,**kw:rows if code==FIRST.corp_code and kw['disclosure_type']=='A' else []
    recent=RecentDisclosureService(settings,client_factory=lambda s:recent_client,clock=lambda:now)
    body_client=MagicMock();body_client.__enter__.return_value=body_client
    body_client._request.return_value=Mock(content=(
        '<DOCUMENT><TITLE>Browser test only</TITLE><TABLE>'
        '<TR><TH>구분</TH><TH>보유주식수(주)</TH></TR>'
        '<TR><TD>직전보고서</TD><TD>0</TD></TR>'
        '<TR><TD>이번보고서</TD><TD>270</TD></TR>'
        '</TABLE></DOCUMENT>').encode('utf-8'))
    body=DocumentViewService(settings,client_factory=lambda s:body_client)
    api=MagicMock();api.__enter__.return_value=api
    api._json.side_effect=lambda endpoint,params:api_payload(params)
    api.corporate_codes.return_value=[{'corp_code':FIRST.corp_code,'corp_name':FIRST.corp_name,'stock_code':FIRST.stock_code}]
    search=DisclosureSearchService(settings,client_factory=lambda s:api)
    counts={'rag':0}
    def answer(selected,question,progress,**kwargs):
        counts['rag']+=1
        for _ in range(5):
            progress('LLM으로 관련 후보 문서 선별 중');progress('임베딩 및 색인 중')
        progress('근거 기반 답변 생성 중')
        return {'answer':f"검증 전용 분석 [근거 1] · 호출 {counts['rag']}", 'citations':[{'number':1,'id':'doc:internal-test-identifier','title':selected.report_nm,'source':selected.source_url,'source_type':'opendart_document','text':'테스트 전용 근거\n보유 수량 270주\n연락처: 010-0000-0000'}]}
    runtime=MagicMock();runtime.return_value.__enter__.return_value.ask.side_effect=answer
    return rows,settings,recent,body,search,runtime,counts

rows,settings,recent,body,search,runtime,counts=boundaries()
with patch('dart_insight.config.load_settings',return_value=settings),patch('dart_insight.services.runtime.open_store',return_value=(Mock(),Mock())),patch('dart_insight.services.runtime.list_disclosures',return_value=rows),patch('dart_insight.services.runtime.analysis_readiness',side_effect=lambda config,d:(d.rcept_no in {row.rcept_no for row in rows[:2]},'AI 분석 가능' if d in rows[:2] else '본문 미색인 공시')),patch('dart_insight.ui.recent.recent_service',return_value=recent),patch('dart_insight.ui.document_view.document_service',return_value=body),patch('dart_insight.ui.search.search_service',return_value=search),patch('dart_insight.services.runtime.Runtime',runtime),patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected external HTTP')):
    runpy.run_path(str(ROOT/'app.py'),run_name='__main__')
