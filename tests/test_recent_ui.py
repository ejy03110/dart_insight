"""Recent metadata never implies that an unindexed disclosure is RAG-ready."""
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

    def test_explicit_load_three_titles_empty_state_and_navigation_do_not_refetch(self):
        service,client,factory,rows=self.build_recent()
        app=AppTest.from_file(fixture.APP).run()
        factory.assert_not_called()
        self.assertFalse(any('최근 7일간 새로운 공시가 없습니다.'==i.value for i in app.info))
        app.button(key='load_recent').click().run()
        self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('recent_home_005930')]),3)
        self.assertTrue(any('최근 7일간 새로운 공시가 없습니다.'==i.value for i in app.info))
        calls=client.list_disclosures.call_count
        app.text_input(key='home_search').set_value('005930').run()
        app.button(key='load_recent').click().run()
        app.button(key='company_005930').click().run()
        self.assertEqual(app.session_state['ui_page'],'공시 탐색')
        app.radio(key='_explore_scope').set_value('최근 7일 공시').run()
        self.assertTrue(any('미색인 공시' in c.value for c in app.caption))
        self.assertTrue(any('보관된 공시 · AI 분석 가능'==c.value for c in app.caption))
        app.text_input(key='_recent_search').set_value('삼성전자').run()
        self.assertEqual(client.list_disclosures.call_count,calls)
        factory.assert_called_once()
        self.service_rag_not_called()

    def service_rag_not_called(self):
        # setUp supplies the existing Runtime mock; this test's service method has a distinct name.
        self.runtime.assert_not_called()

    def test_unindexed_detail_has_original_title_and_url_but_no_analysis(self):
        service,client,factory,rows=self.build_recent()
        app=AppTest.from_file(fixture.APP).run()
        app.button(key='load_recent').click().run()
        app.button(key='recent_home_005930_'+rows[1].rcept_no).click().run()
        self.assertEqual(app.session_state['ui_page'],'공시 상세')
        self.assertTrue(any(h.value==rows[1].report_nm for h in app.subheader))
        self.assertTrue(any(l.proto.url==rows[1].source_url for l in app.get('link_button')))
        self.assertFalse(any(b.key in ('run_analysis','analyze_recent') for b in app.button))
        self.assertTrue(any('아직 분석 준비가 되지 않은 공시' in i.value for i in app.info))
        self.runtime.assert_not_called()
        app.button(key='back_to_recent').click().run()
        self.assertEqual(app.radio(key='_explore_scope').value,'최근 7일 공시')

    def test_indexed_recent_detail_opens_existing_rag_without_indexing(self):
        _,_,_,rows=self.build_recent()
        app=AppTest.from_file(fixture.APP).run()
        app.button(key='load_recent').click().run()
        app.button(key='recent_home_005930_'+rows[0].rcept_no).click().run()
        app.button(key='analyze_recent').click().run()
        self.assertEqual(app.session_state['chosen_receipt'],rows[0].rcept_no)
        self.runtime.assert_not_called()
        app.button(key='run_analysis').click().run()
        self.assertEqual(self.runtime.return_value.__enter__.return_value.ask.call_args.args[0],rows[0])

    def test_failure_is_not_empty_and_existing_rag_remains_accessible(self):
        self.build_recent(failure=True)
        app=AppTest.from_file(fixture.APP).run()
        app.button(key='load_recent').click().run()
        self.assertFalse(any(i.value=='최근 7일간 새로운 공시가 없습니다.' for i in app.info))
        self.assertTrue(any('최근 공시를 불러오지 못했습니다' in w.value for w in app.warning))
        self.assertFalse(any('test-only-sensitive-error' in w.value for w in app.warning))
        app.button(key='company_005930').click().run()
        app.button(key='open_'+recent_row(1).rcept_no).click().run()
        app.button(key='run_analysis').click().run()
        self.assertEqual(len(app.exception),0)
        self.runtime.return_value.__enter__.return_value.ask.assert_called_once()
