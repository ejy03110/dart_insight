"""Full-period metadata search, URL hydration and explicit detail navigation."""
from datetime import date
import unittest
from unittest.mock import Mock,MagicMock,patch
from streamlit.testing.v1 import AppTest
import test_rag_ui as fixture
from test_disclosure_search import api_payload
from dart_insight.services.disclosure_search import DisclosureSearchService
from dart_insight.config import Settings


class SearchUiTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def build_service(self):
        client=MagicMock();client.__enter__.return_value=client
        client._json.side_effect=lambda endpoint,params:api_payload(params)
        client.corporate_codes.return_value=[{'corp_code':'00126380','corp_name':'삼성전자','stock_code':'005930'}]
        service=DisclosureSearchService(Settings(dart_api_key='test-only'),client_factory=lambda s:client)
        self.enterContext(patch('dart_insight.ui.search.search_service',return_value=service))
        return client

    def start(self):
        app=AppTest.from_file(fixture.APP).run()
        app.radio(key='ui_section').set_value('공시 탐색').run()
        self.assertFalse(any(r.key=='_explore_scope' for r in app.radio))
        return app

    def submit(self,app,company='00126380',keyword=''):
        app.text_input(key='api_company').set_value(company)
        app.date_input(key='api_begin').set_value(date(2020,1,1))
        app.date_input(key='api_end').set_value(date(2020,1,31) if not company else date(2020,12,31))
        app.selectbox(key='api_kind').select('A')
        app.text_input(key='api_keyword').set_value(keyword)
        app.button(key='api_submit').click().run()
        self.assertEqual(len(app.exception),0)

    def test_server_pages_bottom_controls_and_back_restore_query(self):
        client=self.build_service()
        app=self.start();client._json.assert_not_called()
        self.submit(app)
        self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('api_detail_')]),20)
        self.assertEqual(app.main.button[-1].key,'api_last')
        app.button(key='api_next').click().run()
        self.assertEqual(app.session_state['api_page'],2)
        self.assertEqual(app.query_params['p'],['2'])
        app.button(key='api_detail_20200101000021').click().run()
        self.assertEqual(app.session_state['ui_page'],'공시 상세')
        self.assertEqual(app.query_params['r'],['20200101000021'])
        self.assertEqual(app.query_params['origin'],['api'])
        app.button(key='back_to_recent').click().run()
        self.assertEqual(app.session_state['api_page'],2)
        self.assertEqual(app.text_input(key='api_company').value,'00126380')
        self.assertEqual(app.date_input(key='api_begin').value,date(2020,1,1))
        self.assertEqual(client._json.call_count,2)
        app.button(key='api_last').click().run()
        self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('api_detail_')]),5)
        self.assertTrue(app.button(key='api_next').disabled)
        app.button(key='api_first').click().run()
        self.assertEqual(app.session_state['api_page'],1)
        self.assertEqual(client._json.call_count,3)
        self.runtime.assert_not_called()

    def test_period_search_does_not_require_an_existing_chroma_index(self):
        client=self.build_service()
        with patch('dart_insight.services.runtime.open_store',side_effect=RuntimeError('test-only-no-index')):
            app=self.start();self.submit(app)
            self.assertTrue(any('미색인 공시' in c.value for c in app.caption))
            self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('api_detail_')]),20)
            app.button(key='api_detail_20200101000001').click().run()
            self.assertFalse(any(b.key=='analyze_recent' for b in app.button))
            self.runtime.assert_not_called()

    def test_all_companies_keyword_total_and_new_conditions_reset_page(self):
        client=self.build_service();app=self.start()
        self.submit(app,company='',keyword='사업보고서')
        self.assertNotIn('corp_code',client._json.call_args.args[1])
        self.assertTrue(any('총 23건' in c.value for c in app.caption))
        app.button(key='api_next').click().run()
        self.assertEqual(app.session_state['api_page'],2)
        self.submit(app,company='005930',keyword='감사보고서')
        self.assertEqual(app.session_state['api_page'],1)
        self.assertTrue(any('총 22건' in c.value for c in app.caption))
        self.runtime.assert_not_called()

    def test_direct_url_cold_session_restores_detail_and_back_page(self):
        client=self.build_service()
        route={'v':'detail','scope':'all','c':'00126380','begin':'2020-01-01','end':'2020-12-31','ty':'A',
               'p':'2','r':'20200101000021','origin':'api'}
        app=AppTest.from_file(fixture.APP)
        app.query_params.update(route);app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.session_state['recent_detail'].rcept_no,'20200101000021')
        self.assertTrue(any(h.value=='사업보고서 21' for h in app.subheader))
        self.assertEqual(len(app.get('html')),0)  # no automatic original download
        app.button(key='back_to_recent').click().run()
        self.assertEqual(app.session_state['api_page'],2)
        self.assertEqual(app.selectbox(key='api_kind').value,'A')
        client._json.assert_called_once()
        self.runtime.assert_not_called()

    def test_url_navigation_rehydrates_form_no_secret_or_role_escalation(self):
        client=self.build_service();app=self.start();self.submit(app)
        app.query_params.update(v='explore',scope='all',c='',begin='2020-01-01',end='2020-01-31',p='1')
        app.query_params['admin_proof']='forged'
        app.run()
        self.assertEqual(app.text_input(key='api_company').value,'')
        self.assertEqual(app.date_input(key='api_end').value,date(2020,1,31))
        self.assertEqual(len(app.toggle),0)
        app.radio(key='ui_section').set_value('홈').run()
        self.assertNotIn('admin_proof',app.query_params)
        self.assertNotIn('test-only',str(dict(app.query_params)))

    def test_invalid_url_and_empty_result_hide_pagination(self):
        client=self.build_service();app=AppTest.from_file(fixture.APP)
        app.query_params.update(v='explore',scope='all',begin='2099-01-01',end='2099-12-31')
        app.run();self.assertEqual(len(app.exception),0)
        client._json.assert_not_called()
        self.submit(app,company='00126380')
        client._json.side_effect=None;client._json.return_value={'status':'013'}
        self.submit(app,company='005930')
        self.assertTrue(any('조회 조건에 해당하는 API 공시가 없습니다' in i.value for i in app.info))
        self.assertFalse(any(b.key=='api_next' for b in app.button))

    def test_indexed_result_has_direct_analysis_action_and_reuses_automatic_analysis(self):
        client=self.build_service()
        from dart_insight.data.models import Disclosure
        row=api_payload({'page_no':1,'page_count':20})['list'][0]
        known=Disclosure.from_api(row,'A','2026-10-22T00:00:00+00:00')
        with patch('dart_insight.services.runtime.list_disclosures',return_value=[known]):
            app=self.start();self.submit(app)
            app.button(key='api_analyze_'+known.rcept_no).click().run()
            self.assertEqual(app.session_state['chosen_receipt'],known.rcept_no)
            self.assertEqual(app.session_state['ui_page'],'공시 분석')
            self.assertFalse(any(b.key=='run_analysis' for b in app.button))
            self.service.ask.assert_called_once()
            app.run()
            self.service.ask.assert_called_once()
