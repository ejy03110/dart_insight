"""Explicit F2002 local indexing CLI. dry-run never loads/downloads a model."""

import argparse
import json
from pathlib import Path
import sys

from dart_insight.config import ConfigurationError, load_settings
from dart_insight.vectorstore.metadata import (
    MetadataIndexError, index_metadata, input_report, load_metadata, open_metadata_collection,
)


def create_embeddings(settings):
    from dart_insight.vectorstore.embeddings import create_embeddings as create_local_embeddings

    return create_local_embeddings(settings)


def main(argv=None):
    parser = argparse.ArgumentParser(description="F2002: F2001 JSON을 dart_metadata에 임베딩·색인")
    parser.add_argument("--input", type=Path, required=True, help="F2001 collection.json 경로")
    parser.add_argument("--db-dir", type=Path, help="기본 DART_DATA_DIR/chroma")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true", help="키·DB 생성·임베딩 호출 없이 입력 검증")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.batch_size <= 2048:
            raise MetadataIndexError("batch-size는 1~2048이어야 합니다.")
        settings = load_settings()
        documents = load_metadata(args.input)
        report = input_report(documents, settings.embedding_model)
        # Print and flush before any possible model download or local encoding.
        print(json.dumps({"stage": "validated_input", "dry_run": args.dry_run, **report},
                         ensure_ascii=False, indent=2), flush=True)
        if args.dry_run:
            return 0
        collection = open_metadata_collection(args.db_dir or settings.data_dir / "chroma", settings.embedding_model)
        embeddings = create_embeddings(settings)
        embeddings.batch_size = args.batch_size
        result = index_metadata(documents, collection, embeddings, model=settings.embedding_model,
                                batch_size=args.batch_size)
        print(json.dumps({"stage": "indexed", **result}, ensure_ascii=False, indent=2))
        return 0
    except (MetadataIndexError, ConfigurationError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("오류: 입력 파일/DB 경로의 존재·권한·디스크 공간을 확인해 주세요.", file=sys.stderr)
        return 1
    except Exception:
        print("오류: 색인 처리에 실패했습니다. DB 상태·모델 설정을 확인해 주세요. "
              "일부 배치가 저장되었을 수 있으므로 같은 명령으로 재시도하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
