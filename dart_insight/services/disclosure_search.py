"""Paged OpenDART metadata search, independent of embeddings and RAG indexes."""
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import calendar
import logging
import math
import re
from threading import RLock
import time
from zoneinfo import ZoneInfo

from dart_insight.config import ConfigurationError
from dart_insight.data.models import Disclosure, DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError

logger=logging.getLogger('dart_insight.operations')
PAGE_SIZE=20
KEYWORD_LIMIT=1000


class SearchValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SearchQuery:
    company: str
    begin: date
    end: date
    kind: str = ''
    keyword: str = ''

    def __post_init__(self):
        if self.begin>self.end or self.begin<date(1999,1,1) or self.end>datetime.now(ZoneInfo('Asia/Seoul')).date():
            raise SearchValidationError('기간은 1999년 이후부터 오늘까지, 시작일 ≤ 종료일로 입력하세요.')
        if self.kind and self.kind not in DISCLOSURE_TYPES:
            raise SearchValidationError('공시 유형을 확인하세요.')
        if len(self.company)>100 or len(self.keyword)>100:
            raise SearchValidationError('기업 및 검색어는 각각 100자 이내로 입력하세요.')
        if not self.company.strip():
            year=self.begin.year+(self.begin.month+2)//12
            month=(self.begin.month+2)%12+1
            boundary=date(year,month,min(self.begin.day,calendar.monthrange(year,month)[1]))
            if self.end>=boundary or (self.end-self.begin).days>=90:
                raise SearchValidationError('전체 기업 검색은 3개월 이내(최대 90일)로 기간을 좁혀 주세요. 기업을 지정하면 더 긴 기간을 검색할 수 있습니다.')


@dataclass(frozen=True)
class SearchPage:
    state: str
    items: tuple = ()
    page: int = 1
    total: int = 0
    pages: int = 0
    api_total: int = 0
    message: str = ''
    notes: tuple = ()


class DisclosureSearchService:
    def __init__(self,settings,*,client_factory=None,timer=None):
        self.settings=replace(settings,request_interval_seconds=max(.3,settings.request_interval_seconds))
        self._factory=client_factory or DartClient
        self._timer=timer or time.monotonic
        self._cache=OrderedDict();self._lock=RLock()

    def _company(self,client,value):
        value=value.strip()
        if not value:
            return None
        if re.fullmatch(r'[0-9]{8}',value):
            return value  # Explicit official identifier; API errors remain errors.
        rows=client.corporate_codes()
        matches=[r for r in rows if r['stock_code']==value or r['corp_name']==value]
        if not matches:
            matches=[r for r in rows if value.casefold() in r['corp_name'].casefold()]
        if len(matches)!=1:
            raise SearchValidationError('기업을 고유하게 찾을 수 없습니다. 정확한 기업명·6자리 종목코드·8자리 기업코드를 입력하세요.')
        return matches[0]['corp_code']

    def _page(self,client,query,corp,page,size):
        params={'bgn_de':query.begin.strftime('%Y%m%d'),'end_de':query.end.strftime('%Y%m%d'),
                'page_no':page,'page_count':size,'sort':'date','sort_mth':'desc'}
        if corp:params['corp_code']=corp
        if query.kind:params['pblntf_ty']=query.kind
        payload=client._json('list.json',params)
        if payload.get('status')=='013':
            return (),0,0
        try:
            total=int(payload['total_count']);pages=int(payload['total_page'])
            rows=payload['list']
            if total<1 or pages<1 or not isinstance(rows,list) or not rows or len(rows)>size or page>pages:
                raise ValueError()
            collected=datetime.now(timezone.utc).isoformat()
            docs=[]
            for row in rows:
                doc=Disclosure.from_api(row,DISCLOSURE_TYPES.get(query.kind,'유형 미지정'),collected)
                if (corp and doc.corp_code!=corp) or not query.begin.isoformat()<=doc.rcept_dt<=query.end.isoformat():
                    raise ValueError()
                docs.append(doc)
            if len({doc.rcept_no for doc in docs})!=len(docs):
                raise ValueError()
            return tuple(docs),total,pages
        except (KeyError,ValueError,TypeError,AttributeError):
            raise DartError('목록 응답 형식이 올바르지 않습니다.','response') from None

    def search(self,query,page=1):
        if not isinstance(page,int) or not 1<=page<=1000000:
            return SearchPage('error',message='페이지 번호를 확인하세요.')
        key=(query,0 if query.keyword else page)
        with self._lock:
            old=self._cache.get(key)
            ttl=60 if old and old[0]=='error' else 300
            if old and self._timer()-old[2]<ttl:
                self._cache.move_to_end(key)
                return self._slice(old[1],page) if query.keyword and old[0]!='error' else old[1]
            if not self.settings.dart_api_key:
                result=SearchPage('error',message='공시 검색에 필요한 연결 설정이 없습니다. 관리자에게 문의해 주세요.')
            else:
                try:
                    with self._factory(self.settings) as client:
                        corp=self._company(client,query.company)
                        if query.keyword:
                            docs,total,pages=self._page(client,query,corp,1,100)
                            if pages!=math.ceil(total/100):
                                raise SearchValidationError('API의 전체 건수와 페이지 범위가 달라 공시명 검색을 완료하지 못했습니다. 범위를 좁혀 주세요.')
                            if total>KEYWORD_LIMIT:
                                raise SearchValidationError('공시명 검색은 조회 범위가 1,000건 이하일 때 지원합니다. 기업·기간·유형을 좁혀 주세요.')
                            all_docs=list(docs)
                            for index in range(2,pages+1):
                                more,more_total,more_pages=self._page(client,query,corp,index,100)
                                if more_total!=total or more_pages!=pages:
                                    raise SearchValidationError('조회 중 결과가 변경되었습니다. 잠시 후 다시 검색해 주세요.')
                                all_docs.extend(more)
                            unique={d.rcept_no:d for d in all_docs}
                            if len(unique)!=total:
                                raise SearchValidationError('API의 전체 건수와 조회 가능한 결과가 달라 공시명 검색을 완료하지 못했습니다. 범위를 좁혀 주세요.')
                            filtered=tuple(sorted((d for d in unique.values() if query.keyword.casefold() in d.report_nm.casefold()),
                                                  key=lambda d:(d.rcept_dt,d.rcept_no),reverse=True))
                            result=SearchPage('success' if filtered else 'empty',filtered,1,len(filtered),math.ceil(len(filtered)/20),total,
                                notes=('공시명은 API 검색 조건이 아닙니다. 1,000건 이하 메타데이터를 확인해 제목을 검색한 결과입니다.',))
                        else:
                            docs,total,pages=self._page(client,query,corp,page,20)
                            if page>1 and not pages:
                                raise SearchValidationError('해당 페이지를 더 이상 조회할 수 없습니다. 첫 페이지에서 다시 검색해 주세요.')
                            notes=() if pages==math.ceil(total/20) else ('API 전체 건수와 페이지 범위가 다릅니다. 이동은 API가 제공하는 페이지 범위로 제한합니다.',)
                            result=SearchPage('success' if docs else 'empty',docs,page,total,pages,total,notes=notes)
                except SearchValidationError as exc:
                    result=SearchPage('error',message=str(exc))
                except Exception as exc:
                    logger.warning('disclosure_search_failed exception=%s',type(exc).__name__)
                    result=SearchPage('error',message='공시를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.')
            self._cache[key]=(result.state,result,self._timer())
            while len(self._cache)>128:self._cache.popitem(last=False)
            return self._slice(result,page) if query.keyword and result.state!='error' else result

    @staticmethod
    def _slice(result,page):
        if result.pages and page>result.pages:
            return SearchPage('error',message='마지막 페이지를 초과했습니다.')
        return replace(result,items=result.items[(page-1)*20:page*20],page=page)
