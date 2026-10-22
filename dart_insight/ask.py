"""Explicit real E2E invocation; no implicit calls on import or app startup."""
import argparse
import json
import sys

from dart_insight.config import load_settings
from dart_insight.services.runtime import Runtime, list_disclosures, open_store


def main():
    parser = argparse.ArgumentParser(description='공시 선택 → 수집·색인 → LangGraph 근거 답변')
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--question', required=True)
    args = parser.parse_args()
    try:
        settings = load_settings()
        metadata, embeddings = open_store(settings)
        selected = next((d for d in list_disclosures(metadata) if d.rcept_no == args.receipt), None)
        if selected is None:
            raise ValueError('선택한 접수번호의 메타데이터 색인이 없습니다.')
        if not settings.openai_api_key or (not settings.document_bundle and not settings.dart_api_key):
            raise ValueError('GMS 키와 OpenDART 키(또는 실제 DOCUMENT_BUNDLE)를 설정해 주세요.')
        print(f'선택 공시 1건; 모델={settings.embedding_model}; device={settings.embedding_device}', file=sys.stderr)
        with Runtime(settings, metadata, embeddings) as service:
            result = service.ask(selected, args.question, lambda message: print(message, file=sys.stderr))
        print(json.dumps({k: v for k, v in result.items() if k != 'evidence'}, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        # Unexpected provider/DB failures must never reveal credentials via traceback.
        print('실행 실패: 키·네트워크·모델 캐시·입력 공시·DB 설정을 확인해 주세요. 원문/데이터는 보존됩니다.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
