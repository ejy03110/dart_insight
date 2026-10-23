"""Original reading and return navigation are isolated from all RAG work."""
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import Mock,MagicMock,patch
from streamlit.testing.v1 import AppTest

import test_rag_ui as fixture
from test_recent_disclosures import NOW,recent_row
from test_document_view import XML,archive
from dart_insight.config import Settings
from dart_insight.services.recent_disclosures import RecentDisclosureService
from dart_insight.services.document_view import DocumentViewService


class DocumentViewUiTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def services(self):
        rows=[replace(recent_row(i,i%3),disclosure_type='정기공시' if i%2 else '주요사항보고') for i in range(1,46)]
        client=MagicMock();client.__enter__.return_value=client
        client.resolve_company.side_effect=lambda company:{'corp_code':fixture.FIRST.corp_code if company.stock_code=='005930' else '99999999'}
        client.list_disclosures.side_effect=lambda code,*a,**kw:rows if code==fixture.FIRST.corp_code and kw['disclosure_type']=='A' else []
        recent=RecentDisclosureService(Settings(dart_api_key='test-only'),client_factory=lambda s:client,clock=lambda:NOW)
        self.enterContext(patch('dart_insight.ui.recent.recent_service',return_value=recent))
        self.enterContext(patch('dart_insight.services.runtime.list_disclosures',return_value=[fixture.FIRST]))
        body_client=MagicMock();body_client.__enter__.return_value=body_client
        body_client._request.return_value=Mock(content=archive(**{'body.xml':XML}))
        body=DocumentViewService(Settings(dart_api_key='test-only'),client_factory=lambda s:body_client)
        self.enterContext(patch('dart_insight.ui.document_view.document_service',return_value=body))
        return rows,client,body_client

    def test_body_explicit_fetch_cache_and_bottom_back_button(self):
        rows,client,body=self.services()
        app=AppTest.from_file(fixture.APP).run()
        app.button(key='load_recent').click().run()
        selected=next(b.key for b in app.button if b.key and b.key.startswith('recent_home_'))
        app.button(key=selected).click().run()
        body._request.assert_not_called()
        self.assertEqual(app.button(key='back_to_recent').label,'← 공시 목록으로 돌아가기')
        self.assertTrue(any(l.proto.url.startswith('https://dart.fss.or.kr/') for l in app.get('link_button')))
        app.button(key='load_document').click().run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any('1,234' in str(e.proto) for e in app.get('html')))
        self.assertTrue(any('table' in str(e.proto) for e in app.get('html')))
        app.run();app.button(key='load_document').click().run()
        body._request.assert_called_once()
        self.assertIn(rows[0].rcept_no if len(self.service.ask.call_args_list)>1 else rows[-1].rcept_no,app.session_state['analyses'])
        self.assertFalse(any(getattr(b.proto,'autoscroll',False) for b in app.get('flex_container')))
        self.assertEqual(app.main.button[-1].key,'back_to_recent')
        app.button(key='back_to_recent').click().run()
        self.assertEqual(app.session_state['ui_page'],'공시 탐색')
        self.assertFalse(any(r.key=='_explore_scope' for r in app.radio))
        body._request.assert_called_once()

    def test_company_period_type_search_page_and_conversation_restored(self):
        rows,client,body=self.services()
        app=AppTest.from_file(fixture.APP).run()
        app.session_state['chosen_receipt']=fixture.FIRST.rcept_no
        old=[{'role':'user','content':'기존 질문','rcept_no':fixture.FIRST.rcept_no},
             {'role':'assistant','content':'기존 답변','rcept_no':fixture.FIRST.rcept_no,'citations':[]}]
        app.session_state['histories']={fixture.FIRST.rcept_no:old}
        app.session_state['analyses']={fixture.FIRST.rcept_no:{'content':'기존 분석','citations':[]}}
        fixture.explore(app)
        # The integrated form preserves company/date/type/title plus server page.
        app.session_state['api_query']={'company':'삼성전자','begin':'2025-01-01','end':'2025-12-31','kind':'A','keyword':'사업보고서'}
        app.session_state['api_page']=2
        app.session_state['stored_page']=1
        app.session_state['recent_detail']=rows[0]
        app.session_state['detail_origin']='recent'
        app.session_state['recent_return']={'scope':'전체 기간 검색','filters':{'api_query':dict(app.session_state['api_query']),'api_page':2,'stored_page':1}}
        app.session_state['ui_page']='공시 상세'
        app.run()
        app.button(key='load_document').click().run()
        app.button(key='back_to_recent').click().run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.text_input(key='api_company').value,'삼성전자')
        self.assertEqual(app.selectbox(key='api_kind').value,'A')
        self.assertEqual(app.text_input(key='api_keyword').value,'사업보고서')
        self.assertEqual(app.session_state['api_page'],2)
        self.assertEqual(app.session_state['chosen_receipt'],fixture.FIRST.rcept_no)
        self.assertEqual(app.session_state['histories'][fixture.FIRST.rcept_no],old)
        self.assertEqual(app.session_state['analyses'][fixture.FIRST.rcept_no]['content'],'기존 분석')
        self.assertEqual(client.list_disclosures.call_count,30)
        self.assertIn(rows[0].rcept_no if len(self.service.ask.call_args_list)>1 else rows[-1].rcept_no,app.session_state['analyses'])

    def test_failed_original_keeps_official_link_and_navigation(self):
        rows,_,body=self.services()
        body._request.side_effect=RuntimeError('test-only-private-error')
        app=AppTest.from_file(fixture.APP).run()
        app.button(key='load_recent').click().run()
        key=next(b.key for b in app.button if b.key and b.key.startswith('recent_home_'))
        app.button(key=key).click().run()
        app.button(key='load_document').click().run()
        self.assertTrue(any('본문을 불러오지 못했습니다' in i.value for i in app.info))
        self.assertFalse(any('test-only-private-error' in i.value for i in app.info))
        self.assertEqual(len(app.get('link_button')),1)
        app.button(key='back_to_recent').click().run()
        self.assertEqual(app.session_state['ui_page'],'공시 탐색')
        self.assertIn(rows[0].rcept_no if len(self.service.ask.call_args_list)>1 else rows[-1].rcept_no,app.session_state['analyses'])
