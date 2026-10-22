"""Paged synthetic list.json responses; no live OpenDART calls."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,Mock
from dart_insight.config import Settings
from dart_insight.services.disclosure_search import DisclosureSearchService,SearchQuery,SearchValidationError
from dart_insight.opendart.client import DartClient,DartError
from test_opendart import response


def api_payload(params,total=45):
    size=params['page_count'];number=params['page_no']
    rows=[{'corp_code':params.get('corp_code','00126380'),'corp_name':'삼성전자','stock_code':'005930',
           'rcept_no':f'20200101{i:06d}','report_nm':('사업보고서' if i%2 else '감사보고서')+f' {i}',
           'rcept_dt':'20200101'} for i in range((number-1)*size+1,min(number*size,total)+1)]
    return {'status':'000','total_count':total,'total_page':(total-1)//size+1,'list':rows}


class SearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.client=MagicMock();self.client.__enter__.return_value=self.client
        self.client._json.side_effect=lambda endpoint,params:api_payload(params)
        self.client.corporate_codes.return_value=[{'corp_code':'00126380','stock_code':'005930','corp_name':'삼성전자'}]
        self.factory=Mock(return_value=self.client);self.tick=0
        self.service=DisclosureSearchService(Settings(dart_api_key='test-only'),client_factory=self.factory,timer=lambda:self.tick)
        self.query=SearchQuery('00126380',date(2020,1,1),date(2020,12,31),'A')

    def test_old_disclosures_and_server_pages_1_2_last(self):
        for page,count,first in [(1,20,'20200101000001'),(2,20,'20200101000021'),(3,5,'20200101000041')]:
            result=self.service.search(self.query,page)
            self.assertEqual(result.state,'success');self.assertEqual(len(result.items),count)
            self.assertEqual(result.items[0].rcept_no,first)
            self.assertEqual((result.total,result.pages),(45,3))
            params=self.client._json.call_args.args[1]
            self.assertEqual((params['page_no'],params['page_count']),(page,20))
            self.assertEqual(params['bgn_de'],'20200101');self.assertEqual(params['pblntf_ty'],'A')

    def test_all_companies_omit_corp_and_names_codes_are_resolved(self):
        all_query=SearchQuery('',date(2020,1,1),date(2020,1,31))
        self.service.search(all_query)
        self.assertNotIn('corp_code',self.client._json.call_args.args[1])
        self.client.corporate_codes.assert_not_called()
        for company in ['삼성전자','005930']:
            self.service.search(SearchQuery(company,date(2020,1,1),date(2020,1,31)))
            self.assertEqual(self.client._json.call_args.args[1]['corp_code'],'00126380')
        with self.assertRaises(SearchValidationError):
            SearchQuery('',date(2020,1,1),date(2020,12,31))

    def test_keyword_exact_total_bounded_metadata_fallback_and_filters(self):
        query=SearchQuery('005930',date(2020,1,1),date(2020,12,31),'A','사업보고서')
        result=self.service.search(query)
        self.assertEqual(result.total,23);self.assertEqual(result.pages,2)
        self.assertEqual(result.api_total,45)
        self.assertTrue(all('사업보고서' in d.report_nm for d in result.items))
        self.assertTrue(result.notes)
        self.assertEqual(self.client._json.call_args.args[1]['page_count'],100)
        result=self.service.search(query,2)
        self.assertEqual(len(result.items),3)
        self.client._json.assert_called_once()

    def test_keyword_scans_bounded_server_pages_once_for_exact_totals(self):
        self.client._json.side_effect=lambda endpoint,params:api_payload(params,145)
        query=SearchQuery('00126380',date(2020,1,1),date(2020,12,31),'A','사업보고서')
        result=self.service.search(query)
        self.assertEqual((result.total,result.pages),(73,4))
        self.assertEqual(self.client._json.call_count,2)
        result=self.service.search(query,4)
        self.assertEqual(len(result.items),13)
        self.assertEqual(self.client._json.call_count,2)

    def test_keyword_over_limit_refuses_scan_without_false_total(self):
        self.client._json.side_effect=lambda endpoint,params:api_payload(params,1001)
        query=SearchQuery('005930',date(2020,1,1),date(2020,12,31),'A','사업보고서')
        result=self.service.search(query)
        self.assertEqual(result.state,'error');self.assertIn('1,000',result.message)
        self.client._json.assert_called_once()

    def test_empty_and_network_errors_not_conflated_and_cached(self):
        self.client._json.side_effect=None;self.client._json.return_value={'status':'013'}
        self.assertEqual(self.service.search(self.query).state,'empty')
        self.service.search(self.query);self.client._json.assert_called_once()
        self.tick=300;self.client._json.side_effect=DartError('test-only-sensitive','network')
        result=self.service.search(self.query)
        self.assertEqual(result.state,'error');self.assertNotIn('test-only-sensitive',result.message)
        self.tick=359;self.service.search(self.query);self.assertEqual(self.client._json.call_count,2)

    def test_missing_key_invalid_pages_and_ambiguous_company(self):
        service=DisclosureSearchService(Settings(),client_factory=self.factory)
        self.assertEqual(service.search(self.query).state,'error');self.factory.assert_not_called()
        self.assertEqual(self.service.search(self.query,0).state,'error')
        self.client.corporate_codes.return_value=[{'corp_name':'삼성전자','stock_code':'005930','corp_code':'00126380'},
                                                 {'corp_name':'삼성물산','stock_code':'028260','corp_code':'00149655'}]
        result=self.service.search(SearchQuery('삼성',date(2020,1,1),date(2020,1,31)))
        self.assertEqual(result.state,'error');self.assertIn('고유하게',result.message)

    def test_mismatched_totals_and_keyword_partial_results_are_explicit(self):
        self.client._json.side_effect=lambda endpoint,params:{**api_payload(params),'total_page':4}
        self.assertTrue(self.service.search(self.query).notes)
        result=self.service.search(SearchQuery('00126380',date(2020,1,1),date(2020,12,31),'A','보고서'))
        self.assertEqual(result.state,'error')

    def test_real_existing_client_request_path_handles_list_status(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Mock();session.get.return_value=response(api_payload({'page_count':20,'page_no':1,'corp_code':'00126380'}))
            service=DisclosureSearchService(Settings(dart_api_key='test-only',data_dir=Path(temp)),
                client_factory=lambda settings:DartClient(settings,session=session))
            result=service.search(self.query)
            self.assertEqual(result.total,45)
            self.assertEqual(session.get.call_args.kwargs['params']['page_count'],20)
            self.assertTrue(session.get.call_args.args[0].endswith('/api/list.json'))
