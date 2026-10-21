"""Local metadata search using exactly the same BGE-M3 factory as indexing."""

import argparse
import json
from pathlib import Path
import sys

from dart_insight.config import ConfigurationError, load_settings
from dart_insight.vectorstore.embeddings import create_embeddings
from dart_insight.vectorstore.metadata import MetadataIndexError, open_metadata_collection, query_metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="로컬 BGE-M3로 dart_metadata 검색 (RAG 아님)")
    parser.add_argument("--query", required=True)
    parser.add_argument("--db-dir", type=Path)
    parser.add_argument("--corp-code")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        directory = args.db_dir or settings.data_dir / "chroma"
        if not directory.is_dir():
            raise MetadataIndexError("색인 DB가 없습니다. 먼저 index_metadata CLI로 색인하세요.")
        collection = open_metadata_collection(directory, settings.embedding_model, create=False)
        result = query_metadata(collection, args.query, create_embeddings(settings),
                                model=settings.embedding_model, corp_code=args.corp_code, top_k=args.top_k)
        print(json.dumps({"embedding_model": settings.embedding_model, "results": result},
                         ensure_ascii=False, indent=2))
        return 0
    except (MetadataIndexError, ConfigurationError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("오류: 로컬 검색에 실패했습니다. 기존 DB·컬렉션·모델 설정을 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
