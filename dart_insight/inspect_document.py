"""Offline diagnostics for an actual failing OpenDART ZIP; no key or API required."""
import argparse
import json
from pathlib import Path

from dart_insight.opendart.client import DartError
from dart_insight.services.original_documents import MAX_BYTES, parse_document_zip


def main(argv=None):
    parser = argparse.ArgumentParser(description='원문 ZIP 구조/인코딩/XML 오류 진단 (본문·키 출력 없음)')
    parser.add_argument('--input', required=True, type=Path)
    args = parser.parse_args(argv)
    report = {'members': []}
    try:
        size = args.input.stat().st_size
        report['zip_bytes'] = size
        if size > MAX_BYTES:
            raise DartError('ZIP 다운로드 크기가 100 MiB 제한을 초과했습니다.', 'document_size')
        text = parse_document_zip(args.input.read_bytes(), diagnostics=report['members'])
        report.update(status='ok', text_characters=len(text))
        code = 0
    except DartError as exc:
        report.update(status='error', code=exc.code, message=str(exc))
        code = 1
    except OSError:
        report.update(status='error', code='file', message='입력 ZIP 파일 경로·읽기 권한을 확인해 주세요.')
        code = 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
