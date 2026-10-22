"""Offline diagnostics for an actual failing OpenDART ZIP; no key or API required."""
import argparse
import json
from pathlib import Path

from dart_insight.opendart.client import DartError
from dart_insight.services.original_documents import MAX_BYTES, parse_document_zip


def main(argv=None):
    parser = argparse.ArgumentParser(description='원문 ZIP 구조/인코딩/XML 오류 진단 (본문·키 출력 없음)')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--show-repairs', action='store_true', help='복구된 제목의 짧은 예시 표시 (본문 일부)')
    parser.add_argument('--text-output', type=Path, help='모든 XML 검사 성공 시 실제 본문을 새 UTF-8 파일로 저장 (덮어쓰기 없음)')
    args = parser.parse_args(argv)
    report = {'members': []}
    try:
        size = args.input.stat().st_size
        report['zip_bytes'] = size
        if size > MAX_BYTES:
            raise DartError('ZIP 다운로드 크기가 100 MiB 제한을 초과했습니다.', 'document_size')
        text = parse_document_zip(args.input.read_bytes(), diagnostics=report['members'])
        if args.text_output:
            with args.text_output.open('x', encoding='utf-8') as stream:
                stream.write(text)
        report.update(status='ok', text_characters=len(text), xml_files_checked=len(report['members']),
                      xml_files_dropped=0, text_saved=bool(args.text_output))
        code = 0
    except DartError as exc:
        report.update(status='error', code=exc.code, message=str(exc))
        code = 1
    except OSError:
        report.update(status='error', code='file', message='입력 ZIP/새 출력 경로·권한을 확인하세요. 기존 파일은 덮어쓰지 않습니다.')
        code = 1
    if not args.show_repairs:
        for member in report['members']:
            member.pop('repair_examples', None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
