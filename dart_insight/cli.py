"""Read-only connection probe. Not the later 100+ item ingestion/indexing job."""

import argparse
from datetime import date, timedelta
import json
import sys

from dart_insight.config import ConfigurationError, load_settings
from dart_insight.data.models import COMPANIES, DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError


def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenDART 기업코드/공시 메타데이터 연결 확인")
    parser.add_argument("--company", choices=[c.name for c in COMPANIES], default="삼성전자")
    parser.add_argument("--begin", type=date.fromisoformat, default=date.today() - timedelta(days=365))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--type", choices=list(DISCLOSURE_TYPES), default="A", dest="disclosure_type")
    parser.add_argument("--max-pages", type=int, default=1, help="연결 확인 기본값은 1페이지(최대 100건)")
    parser.add_argument("--refresh", action="store_true", help="기존 캐시를 사용하지 않고 API 호출")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        company = next(c for c in COMPANIES if c.name == args.company)
        with DartClient(settings) as client:
            corp = client.resolve_company(company, refresh=args.refresh)
            items = client.list_disclosures(corp["corp_code"], args.begin, args.end,
                                           disclosure_type=args.disclosure_type,
                                           max_pages=args.max_pages, refresh=args.refresh)
        print(json.dumps({"company": company.name, "corp_code": corp["corp_code"],
                          "count": len(items), "max_pages": args.max_pages,
                          "note": "페이지 제한이 적용된 연결 확인입니다. 100건 확보·색인을 뜻하지 않습니다.",
                          "disclosures": [item.to_dict() for item in items]},
                         ensure_ascii=False, indent=2))
        return 0
    except (ConfigurationError, DartError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("오류: 로컬 캐시 경로의 쓰기 권한과 디스크 공간을 확인해 주세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
