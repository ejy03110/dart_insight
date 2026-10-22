"""Synthetic recent metadata only; no live API, database, or model operations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock

from dart_insight.config import Settings
from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError
from dart_insight.services.recent_disclosures import RecentDisclosureService, latest_three, KST
from test_rag_ui import FIRST
from test_opendart import corp_zip, page, response, row

NOW=datetime(2026,10,22,12,tzinfo=KST)


def recent_row(index, days=0):
    receipt=f'20261022{index:06d}'
    return replace(FIRST,rcept_no=receipt,report_nm=f'합성 테스트 공시 {index}',
                   rcept_dt=(NOW.date()-timedelta(days=days)).isoformat(),
                   source_url=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}')


class RecentServiceTests(unittest.TestCase):
    def setUp(self):
        self.client=MagicMock()
        self.client.resolve_company.return_value={'corp_code':FIRST.corp_code}
        self.client.list_disclosures.return_value=[]
        self.factory=Mock(return_value=self.client)
        self.client.__enter__.return_value=self.client
        self.tick=0
        self.now=NOW
        self.service=RecentDisclosureService(Settings(dart_api_key='test-only-dart'), companies=COMPANIES[:1],
            client_factory=self.factory, clock=lambda:self.now, timer=lambda:self.tick)

    def test_success_sort_deduplicate_and_three_item_limit(self):
        rows=[recent_row(1,2),recent_row(2),recent_row(3),recent_row(4,1)]
        self.client.list_disclosures.side_effect=lambda *args,**kwargs: rows if kwargs['disclosure_type']=='A' else [rows[0]]
        result=self.service.refresh()['005930']
        self.assertEqual(result.state,'success')
        self.assertEqual([d.rcept_no for d in result.disclosures],[rows[i].rcept_no for i in [2,1,3,0]])
        self.assertEqual(len(latest_three(result)),3)
        self.assertEqual(result.disclosures[0].report_nm,rows[2].report_nm)
        self.assertEqual(result.begin,NOW.date()-timedelta(days=6))
        self.assertEqual(self.client.list_disclosures.call_count,len(DISCLOSURE_TYPES))
        for call in self.client.list_disclosures.call_args_list:
            self.assertIsNone(call.kwargs['max_pages'])
        self.assertEqual(self.factory.call_args.args[0].request_interval_seconds,.3)

    def test_empty_requires_all_types_to_succeed(self):
        self.assertEqual(self.service.refresh()['005930'].state,'empty')
        self.assertEqual(self.client.list_disclosures.call_count,10)

    def test_failed_type_does_not_publish_partial_data_as_success_or_empty(self):
        self.client.list_disclosures.side_effect=[ [recent_row(1)], DartError('test-only-error','network')]
        result=self.service.refresh()['005930']
        self.assertEqual(result.state,'error')
        self.assertEqual(result.disclosures,())
        self.assertNotIn('test-only-error',str(result))

    def test_cache_backoff_expiry_and_concurrent_refreshes(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.service.refresh(),range(4)))
        self.assertTrue(all(r['005930'].state=='empty' for r in results))
        self.factory.assert_called_once()
        self.tick=299
        self.service.peek();self.service.refresh()
        self.factory.assert_called_once()
        self.tick=300
        self.client.list_disclosures.side_effect=DartError('no','network')
        self.assertEqual(self.service.refresh()['005930'].state,'error')
        self.tick=359;self.service.refresh()
        self.assertEqual(self.factory.call_count,2)
        self.tick=360;self.service.refresh()
        self.assertEqual(self.factory.call_count,3)

    def test_date_rollover_uses_korean_date_and_peek_never_fetches(self):
        self.service.refresh()
        self.now=datetime(2026,10,22,15,5,tzinfo=timezone.utc)
        result=self.service.peek()['005930']
        self.assertEqual(result.state,'unloaded')
        self.assertEqual(result.end.isoformat(),'2026-10-23')
        self.assertEqual(result.begin.isoformat(),'2026-10-17')
        self.factory.assert_called_once()
        self.service.refresh()
        self.assertEqual(self.factory.call_count,2)

    def test_missing_key_does_not_construct_client(self):
        service=RecentDisclosureService(Settings(),client_factory=self.factory,clock=lambda:NOW)
        self.assertTrue(all(r.state=='error' for r in service.refresh().values()))
        self.factory.assert_not_called()

    def test_global_limit_stops_remaining_companies(self):
        service=RecentDisclosureService(Settings(dart_api_key='test-only'),client_factory=self.factory,clock=lambda:NOW)
        self.client.resolve_company.side_effect=DartError('test-only','020')
        result=service.refresh()
        self.assertTrue(all(r.state=='error' for r in result.values()))
        self.client.resolve_company.assert_called_once()
        self.client.list_disclosures.assert_not_called()

    def test_one_company_failure_does_not_fail_other_companies(self):
        service=RecentDisclosureService(Settings(dart_api_key='test-only'),client_factory=self.factory,clock=lambda:NOW)
        self.client.resolve_company.side_effect=[DartError('test-only','company'),{'corp_code':'00164742'},{'corp_code':'00429367'}]
        result=service.refresh()
        self.assertEqual([r.state for r in result.values()],['error','empty','empty'])

    def test_out_of_window_or_wrong_company_fails(self):
        for bad in [recent_row(1,7),replace(recent_row(1),corp_code='99999999')]:
            self.tick+=60
            self.client.list_disclosures.side_effect=None
            self.client.list_disclosures.return_value=[bad]
            self.assertEqual(self.service.refresh()['005930'].state,'error')

    def test_existing_client_pagination_all_types_and_temporary_disk_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            session=Mock()
            first=row('20261022000001');first['rcept_dt']='20261022'
            second=row('20261021000002');second['rcept_dt']='20261021'
            session.get.side_effect=[response(content=corp_zip()),page([first],total=2),page([second],total=2),
                                     *[response({'status':'013'}) for _ in range(9)]]
            factory=lambda settings:DartClient(settings,session=session)
            service=RecentDisclosureService(Settings(dart_api_key='test-only',data_dir=Path(temp)),companies=COMPANIES[:1],
                                            client_factory=factory,clock=lambda:NOW)
            result=service.refresh()['005930']
            self.assertEqual(result.state,'success')
            self.assertEqual(len(result.disclosures),2)
            self.assertEqual(session.get.call_count,12)
            self.assertTrue(list((Path(temp)/'cache'/'disclosures').glob('*.json')))
            service.refresh();service.peek()
            self.assertEqual(session.get.call_count,12)
