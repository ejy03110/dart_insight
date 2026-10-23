"""Automatic recent cards, cache reuse, body readiness and error/empty separation."""
import unittest
from unittest.mock import patch, MagicMock, Mock
from streamlit.testing.v1 import AppTest
import test_rag_ui as fixture
from test_recent_disclosures import NOW, recent_row
from dart_insight.config import Settings
from dart_insight.data.models import COMPANIES
from dart_insight.services.recent_disclosures import RecentDisclosureService


class RecentUiTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def build_recent(self, failure=False):
        client=MagicMock();client.__enter__.return_value=client
        client.resolve_company.side_effect=lambda company:{'corp_code':fixture.FIRST.corp_code if company==COMPANIES[0] else '99999999'}
        rows=[recent_row(1),recent_row(2),recent_row(3,1),recent_row(4,2)]
        client.list_disclosures.side_effect=(RuntimeError('test-only-sensitive-error') if failure else
            lambda corp,*args,**kw: rows if corp==fixture.FIRST.corp_code and kw['disclosure_type']=='A' else [])
        factory=Mock(return_value=client)
        service=RecentDisclosureService(Settings(dart_api_key='test-only'),client_factory=factory,clock=lambda:NOW)
        self.enterContext(patch('dart_insight.ui.recent.recent_service',return_value=service))
        self.enterContext(patch('dart_insight.services.runtime.list_disclosures',return_value=[rows[0]]))
        return service,client,factory,rows

    def test_automatic_load_carousel_empty_state_and_navigation_do_not_refetch(self):
        service,client,factory,rows=self.build_recent()
        with patch('dart_insight.services.runtime.analysis_readiness', return_value=(False,'본문 미색인 공시')):
            app=AppTest.from_file(fixture.APP).run()
            factory.assert_called_once()
            self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('recent_home_005930')]),3)
            calls=client.list_disclosures.call_count
            self.assertTrue(any(i.value=='최근 7일간 새로운 공시가 없습니다.' for i in app.info))
            app.text_input(key='home_search').set_value('005930').run()
            app.button(key='load_recent').click().run()
            self.assertEqual(client.list_disclosures.call_count,calls)
            self.service.ask.assert_called_once()

    def test_unindexed_detail_automatically_prepares_analysis_without_action_buttons(self):
        _,_,_,rows=self.build_recent()
        with patch('dart_insight.services.runtime.analysis_readiness', return_value=(False,'본문 미색인 공시')):
            app=AppTest.from_file(fixture.APP).run()
            app.button(key='recent_home_005930_'+rows[1].rcept_no).click().run()
            self.assertEqual(app.session_state['ui_page'],'공시 상세')
            self.assertTrue(any(h.value==rows[1].report_nm for h in app.subheader))
            self.assertTrue(any(l.proto.url==rows[1].source_url for l in app.get('link_button')))
            self.assertFalse(any(b.key in ('run_analysis','analyze_recent') for b in app.button))
            self.assertIn(rows[1].rcept_no,app.session_state['analyses'])
            self.assertFalse(app.chat_input[0].proto.disabled)
            self.assertEqual(self.service.ask.call_count,1)  # homepage picked this same latest item
            app.button(key='back_to_recent').click().run()
            self.assertFalse(any(r.key=='_explore_scope' for r in app.radio))

    def test_indexed_recent_detail_reuses_one_home_briefing_and_keeps_chat_isolated(self):
        _,_,_,rows=self.build_recent()
        app=AppTest.from_file(fixture.APP).run()
        self.service.ask.assert_called_once()
        self.assertEqual(self.service.ask.call_args.args[0],rows[1])
        app.run()
        app.button(key='recent_home_005930_'+rows[1].rcept_no).click().run()
        self.service.ask.assert_called_once()
        self.assertEqual(len(app.chat_message),0)
        app.chat_input[0].set_value('추가 질문').run()
        self.assertEqual(self.service.ask.call_count,2)
        self.assertEqual(len(app.chat_message),2)

    def test_failure_is_not_empty_and_existing_rag_remains_accessible(self):
        self.build_recent(failure=True)
        app=AppTest.from_file(fixture.APP).run()
        self.assertFalse(any(i.value=='최근 7일간 새로운 공시가 없습니다.' for i in app.info))
        self.assertTrue(any('최근 공시를 불러오지 못했습니다' in w.value for w in app.warning))
        self.assertFalse(any('test-only-sensitive-error' in w.value for w in app.warning))
        fixture.open_disclosure(app,recent_row(1).rcept_no)
        self.assertEqual(len(app.exception),0)
        self.service.ask.assert_called_once()
