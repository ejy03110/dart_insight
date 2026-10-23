"""Read-only recent metadata, isolated from RAG and indexes. Refresh is explicit and TTL-bound."""
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import logging
from threading import RLock
import time
from zoneinfo import ZoneInfo

from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES, Company, Disclosure
from dart_insight.opendart.client import DartClient, DartError

CACHE_SECONDS = 300
ERROR_RETRY_SECONDS = 60
KST = ZoneInfo('Asia/Seoul')
logger = logging.getLogger('dart_insight.operations')
STOP_CODES = {'010', '011', '012', '020', '101', '901'}


@dataclass(frozen=True)
class RecentResult:
    company: Company
    begin: date
    end: date
    state: str = 'unloaded'
    disclosures: tuple[Disclosure, ...] = ()
    checked_at: datetime | None = None
    error: str = ''


def latest_three(result):
    return tuple(sorted(result.disclosures, key=lambda d: (d.rcept_dt, d.rcept_no), reverse=True)[:3])


class RecentDisclosureService:
    """One lock coalesces concurrent refreshes. Per-company failures never mean empty.

    In-memory results include errors for backoff; the existing DartClient also
    retains its success-only disk cache. No Chroma, embeddings or LLM imports.
    """
    def __init__(self, settings, companies=COMPANIES, *, client_factory=None, clock=None, timer=None):
        self.settings = replace(settings, list_cache_ttl_seconds=CACHE_SECONDS,
                                request_interval_seconds=max(.3, settings.request_interval_seconds))
        self.companies = tuple(companies)
        self._factory = client_factory or DartClient
        self._clock = clock or (lambda: datetime.now(KST))
        self._timer = timer or time.monotonic
        self._results = {}
        self._lock = RLock()

    def _window(self):
        now = self._clock().astimezone(KST)
        return now, now.date()-timedelta(days=6), now.date()

    def _snapshot(self, begin, end):
        return {c.stock_code: self._results.get((c.stock_code,end), (RecentResult(c,begin,end),0))[0]
                for c in self.companies}

    def peek(self):
        """No network or disk writes, including on expiry and date rollover."""
        _, begin, end = self._window()
        with self._lock:
            return self._snapshot(begin,end)

    def refresh(self):
        """Refresh on caller request; coalesce automatic home loads and manual refreshes."""
        with self._lock:
            now, begin, end = self._window()
            self._results = {key:value for key,value in self._results.items() if key[1]==end}
            tick = self._timer()
            pending = []
            for company in self.companies:
                previous = self._results.get((company.stock_code,end))
                wait = ERROR_RETRY_SECONDS if previous and previous[0].state=='error' else CACHE_SECONDS
                if previous is None or tick-previous[1]>=wait:
                    pending.append(company)
            if not pending:
                return self._snapshot(begin,end)

            def save(company, state, rows=(), error=''):
                self._results[(company.stock_code,end)] = (
                    RecentResult(company,begin,end,state,tuple(rows),now,error), self._timer())

            if not self.settings.dart_api_key:
                for company in pending:
                    save(company,'error',error='missing_credentials')
                return self._snapshot(begin,end)
            try:
                with self._factory(self.settings) as client:
                    blocked = False
                    for company in pending:
                        if blocked:
                            save(company,'error',error='unavailable')
                            continue
                        try:
                            # Official mapping + corporate-code cache; never invent corp codes.
                            corp = client.resolve_company(company)
                            rows = {}
                            for kind in DISCLOSURE_TYPES:
                                # Bypass only the 5-minute recent list cache after its TTL.
                                # Corporate mapping retains the existing longer-lived cache.
                                disclosures = client.list_disclosures(corp['corp_code'],begin,end,
                                    disclosure_type=kind,max_pages=None,refresh=True)
                                for row in disclosures:
                                    if row.corp_code != corp['corp_code'] or not begin.isoformat()<=row.rcept_dt<=end.isoformat():
                                        raise DartError('공시 조회 범위가 일치하지 않습니다.','response')
                                    rows.setdefault(row.rcept_no, row if row.stock_code else replace(row,stock_code=company.stock_code))
                            ordered = sorted(rows.values(),key=lambda d:(d.rcept_dt,d.rcept_no),reverse=True)
                            save(company,'success' if ordered else 'empty',ordered)
                        except Exception as exc:
                            code = exc.code if isinstance(exc,DartError) else 'processing'
                            logger.warning('recent_disclosures_failed exception=%s',type(exc).__name__)
                            save(company,'error',error=code)
                            blocked = code in STOP_CODES
            except Exception as exc:
                logger.warning('recent_disclosures_unavailable exception=%s',type(exc).__name__)
                for company in pending:
                    save(company,'error',error='unavailable')
            return self._snapshot(begin,end)
