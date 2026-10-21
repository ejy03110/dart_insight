"""F2001 CLI: real metadata collection, not embedding or document downloading."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from dart_insight.config import ConfigurationError, load_settings
from dart_insight.data.collection import collect_metadata, save_collection
from dart_insight.data.models import DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError


def main(argv=None):
    parser = argparse.ArgumentParser(description="F2001: 3개 기업 공시 100건 이상 수집·저장")
    parser.add_argument("--begin", type=date.fromisoformat, required=True, help="최초 조회 시작일 YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="조회 종료일 YYYY-MM-DD")
    parser.add_argument("--types", nargs="+", choices=list(DISCLOSURE_TYPES),
                        default=list(DISCLOSURE_TYPES), help="기본 A~J 전체 유형")
    parser.add_argument("--minimum-count", type=int, default=100, help="최소 100, 기본 100")
    parser.add_argument("--earliest", type=date.fromisoformat, default=date(2000, 1, 1),
                        help="자동 기간 확대의 시작일 하한, 기본 2000-01-01")
    parser.add_argument("--output-dir", type=Path, help="기본 DART_DATA_DIR/metadata, 실행별 새 폴더 생성")
    parser.add_argument("--refresh", action="store_true", help="기존 캐시를 무시하고 API 호출")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        with DartClient(settings) as client:
            result = collect_metadata(
                client, args.begin, args.end, disclosure_types=args.types,
                minimum_count=args.minimum_count, earliest=args.earliest, refresh=args.refresh,
                progress=lambda message: print(message, file=sys.stderr),
            )
        path = save_collection(result, args.output_dir or settings.data_dir / "metadata")
        print(json.dumps({"output_path": str(path.resolve()), **result["summary"]},
                         ensure_ascii=False, indent=2))
        if result["summary"]["status"] != "complete":
            print("미완료: 조회 하한까지 확대했지만 고유 공시 수 또는 3개 기업의 자료가 부족합니다. "
                  "저장 파일은 성공이 아닌 insufficient_data 결과입니다.", file=sys.stderr)
            return 2
        return 0
    except (ConfigurationError, DartError) as exc:
        print(f"오류: {exc} 이번 실행의 수집 결과는 저장하지 않았습니다.", file=sys.stderr)
        return 1
    except OSError:
        print("오류: 수집 결과를 저장하지 못했습니다. 로컬 경로의 권한과 공간을 확인해 주세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
