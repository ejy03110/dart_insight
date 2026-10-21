"""Export actual OpenDART originals for the PDF's permitted offline-bundle workflow."""
import argparse
import json
from pathlib import Path
import sys

from dart_insight.config import load_settings
from dart_insight.services.original_documents import OpenDartSource, selected_candidate
from dart_insight.services.runtime import list_disclosures, open_store


def main():
    parser = argparse.ArgumentParser(description='색인된 접수번호의 실제 공시 원문을 문서 묶음으로 내보냅니다.')
    parser.add_argument('--receipt', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = None
    try:
        settings = load_settings()
        metadata, _ = open_store(settings)  # model construction is lazy; no embedding occurs
        by_id = {d.rcept_no: d for d in list_disclosures(metadata)}
        if any(receipt not in by_id for receipt in args.receipt):
            raise ValueError('색인된 접수번호를 사용해 주세요.')
        if args.output.exists():
            raise ValueError('기존 파일은 덮어쓰지 않습니다. 새 출력 경로를 사용해 주세요.')
        source = OpenDartSource(settings)
        documents = [source.collect(selected_candidate(by_id[r])).to_dict() for r in dict.fromkeys(args.receipt)]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump({'schema_version': 1, 'documents': documents}, stream, ensure_ascii=False, indent=2)
        print(f'실제 공시 원문 {len(documents)}건 저장 완료')
        return 0
    except Exception:
        print('문서 내보내기 실패: 키·네트워크·접수번호·새 출력 경로를 확인해 주세요.', file=sys.stderr)
        return 1
    finally:
        if source:
            source.close()


if __name__ == '__main__':
    raise SystemExit(main())
