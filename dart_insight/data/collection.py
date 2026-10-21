"""F2001: collect all pages for the three companies and persist an auditable snapshot."""

import calendar
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterable

from dart_insight.config import ConfigurationError
from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES, Disclosure
from dart_insight.opendart.client import DartClient, DartError


def _previous_year(day: date) -> date:
    if day.year == 1:
        return date.min
    year = day.year - 1
    return date(year, day.month, min(day.day, calendar.monthrange(year, day.month)[1]))


def collect_metadata(
    client: DartClient,
    begin: date,
    end: date,
    *,
    disclosure_types: Iterable[str] = tuple(DISCLOSURE_TYPES),
    minimum_count: int = 100,
    earliest: date = date(2000, 1, 1),
    refresh: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Finish every company/type/page in a window before evaluating the count.

    Expand backwards in disjoint one-year windows. All three companies must have
    at least one disclosure; no sample or fabricated rows are used on failure.
    A bounded shortfall returns an explicitly insufficient snapshot. API errors
    propagate immediately, so an interrupted collection is never called complete.
    """
    types = tuple(dict.fromkeys(disclosure_types))
    if begin > end or earliest > begin:
        raise ConfigurationError("earliest ≤ begin ≤ end 순서로 조회 기간을 설정해 주세요.")
    if minimum_count < 100:
        raise ConfigurationError("F2001 최소 고유 공시 수는 100건 이상이어야 합니다.")
    if not types or any(kind not in DISCLOSURE_TYPES for kind in types):
        raise ConfigurationError("공시 유형은 A~J 중 하나 이상이어야 합니다.")

    # Reuse the existing official-stock-code resolver and corporate-code cache.
    # Even --refresh downloads that large ZIP only once with the default TTL.
    corporations = {
        company.stock_code: client.resolve_company(company, refresh=refresh and index == 0)
        for index, company in enumerate(COMPANIES)
    }
    items: dict[str, Disclosure] = {}
    labels: dict[str, set[str]] = {}
    queries = []
    windows = []
    window_begin, window_end = begin, end
    while True:
        windows.append({"begin": window_begin.isoformat(), "end": window_end.isoformat()})
        for company in COMPANIES:
            corp = corporations[company.stock_code]
            for kind in types:
                if progress:
                    progress(f"{company.name} / {DISCLOSURE_TYPES[kind]} / "
                             f"{window_begin}~{window_end} 전체 페이지 조회")
                records = client.list_disclosures(
                    corp["corp_code"], window_begin, window_end,
                    disclosure_type=kind, max_pages=None, refresh=refresh,
                )
                for item in records:
                    if (item.corp_code != corp["corp_code"]
                            or item.stock_code not in ("", company.stock_code)
                            or not window_begin.isoformat() <= item.rcept_dt <= window_end.isoformat()):
                        raise DartError("조회 조건과 일치하지 않는 공시 응답을 받았습니다.", "response")
                    previous = items.get(item.rcept_no)
                    if previous and previous.corp_code != item.corp_code:
                        raise DartError("동일 접수번호에 서로 다른 기업이 반환되었습니다.", "response")
                    # A receipt may appear in multiple type queries; retain every
                    # observed category without creating duplicate disclosures.
                    items.setdefault(item.rcept_no, replace(item, stock_code=company.stock_code))
                    labels.setdefault(item.rcept_no, set()).add(DISCLOSURE_TYPES[kind])
                queries.append({
                    "company": company.name, "stock_code": company.stock_code,
                    "corp_code": corp["corp_code"], "type_code": kind,
                    "begin": window_begin.isoformat(), "end": window_end.isoformat(),
                    "unique_count": len({record.rcept_no for record in records}),
                })

        counts = {
            company.name: sum(item.corp_code == corporations[company.stock_code]["corp_code"]
                              for item in items.values())
            for company in COMPANIES
        }
        complete = len(items) >= minimum_count and all(counts.values())
        if complete or window_begin == earliest:
            break
        window_end = window_begin - timedelta(days=1)
        window_begin = max(earliest, _previous_year(window_begin))

    ordered = sorted(items.values(), key=lambda item: (item.rcept_dt, item.rcept_no), reverse=True)
    documents = []
    for item in ordered:
        item = replace(item, disclosure_type=", ".join(sorted(labels[item.rcept_no])))
        documents.append({**item.to_dict(), "index_text": item.to_index_text()})
    return {
        "schema_version": 1,
        "requirement": "F2001",
        "summary": {
            "status": "complete" if complete else "insufficient_data",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "minimum_count": minimum_count,
            "total_unique_count": len(documents),
            "company_counts": counts,
            "companies": [{"name": company.name, "stock_code": company.stock_code,
                           **corporations[company.stock_code]} for company in COMPANIES],
            "requested_period": {"begin": begin.isoformat(), "end": end.isoformat()},
            "queried_period": {"begin": window_begin.isoformat(), "end": end.isoformat()},
            "earliest_allowed": earliest.isoformat(),
            "disclosure_types": list(types),
            "refresh_requested": refresh,
            "queried_windows": windows,
            "queries": queries,
            "sample_index_text": documents[0]["index_text"] if documents else None,
        },
        "disclosures": documents,
    }


def save_collection(result: dict, directory: Path) -> Path:
    """Write one atomic JSON bundle per run; never overwrite earlier evidence."""
    directory.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=directory))
    temporary = run_dir / "collection.tmp"
    target = run_dir / "collection.json"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        run_dir.rmdir()
        raise
    return target
