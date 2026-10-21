"""F2002: validated F2001 input and idempotent, persistent metadata indexing.

No OpenAI client is created here. Embeddings are injected by the explicit CLI
or by offline tests; Chroma's default embedding function is always disabled.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from dart_insight.data.models import COMPANIES

COLLECTION_NAME = "dart_metadata"
REQUIRED_FIELDS = ("corp_code", "corp_name", "rcept_no", "report_nm", "rcept_dt",
                   "source_url", "disclosure_type", "index_text")


class MetadataIndexError(ValueError):
    """Validation/indexing errors with no raw credentials or API responses."""


class Embeddings(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class MetadataDocument:
    id: str
    text: str
    metadata: dict

    @property
    def content_hash(self):
        value = json.dumps({"text": self.text, "metadata": self.metadata},
                           ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(value.encode()).hexdigest()


def load_metadata(path: Path) -> list[MetadataDocument]:
    """Reject incomplete, duplicate or inconsistent snapshots before any API/DB work."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise MetadataIndexError("입력 파일이 올바른 UTF-8 JSON이 아닙니다.") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("requirement") != "F2001":
        raise MetadataIndexError("F2001 schema_version=1 수집 결과 JSON이 필요합니다.")
    summary = data.get("summary")
    rows = data.get("disclosures")
    if not isinstance(summary, dict) or summary.get("status") != "complete":
        raise MetadataIndexError("summary.status=complete인 수집 결과만 색인할 수 있습니다.")
    if not isinstance(rows, list) or len(rows) < 100 or summary.get("total_unique_count") != len(rows):
        raise MetadataIndexError("입력은 100건 이상이어야 하며 summary 총건수와 실제 건수가 일치해야 합니다.")
    documents, seen = [], set()
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                                            for key in REQUIRED_FIELDS):
            raise MetadataIndexError(f"{number}번째 공시의 필수 문자열 필드가 누락되었거나 비어 있습니다.")
        if not re.fullmatch(r"\d{8}", row["corp_code"]) or not re.fullmatch(r"\d{14}", row["rcept_no"]):
            raise MetadataIndexError(f"{number}번째 공시의 기업코드/접수번호 형식이 잘못되었습니다.")
        try:
            if date.fromisoformat(row["rcept_dt"]).isoformat() != row["rcept_dt"]:
                raise ValueError()
            url = urlparse(row["source_url"])
            if (url.scheme != "https" or url.hostname != "dart.fss.or.kr" or url.username or url.password
                    or parse_qs(url.query).get("rcpNo") != [row["rcept_no"]]
                    or set(parse_qs(url.query)) != {"rcpNo"}):
                raise ValueError()
        except ValueError:
            raise MetadataIndexError(f"{number}번째 공시의 접수일/키 없는 원문 URL을 확인해 주세요.") from None
        if any(row[key] not in row["index_text"] for key in
               ("corp_name", "report_nm", "rcept_no", "rcept_dt", "disclosure_type", "source_url")):
            raise MetadataIndexError(f"{number}번째 색인 텍스트에 필수 메타데이터 내용이 없습니다.")
        identifier = f"meta:{row['rcept_no']}"
        if identifier in seen:
            raise MetadataIndexError("중복 접수번호가 있습니다. 고유 공시 입력을 사용해 주세요.")
        seen.add(identifier)
        # Allowlist only: unknown input fields (including any credentials) are not persisted.
        metadata = {key: row[key] for key in REQUIRED_FIELDS if key != "index_text"}
        metadata["source_type"] = "opendart_metadata"
        documents.append(MetadataDocument(identifier, row["index_text"], metadata))

    companies = summary.get("companies")
    if not isinstance(companies, list) or len(companies) != len(COMPANIES):
        raise MetadataIndexError("summary.companies에 세 기업의 종목코드/기업코드 매핑이 필요합니다.")
    expected_counts = {}
    codes = set()
    for company in COMPANIES:
        matches = [c for c in companies if isinstance(c, dict) and c.get("stock_code") == company.stock_code]
        if len(matches) != 1 or not isinstance(matches[0].get("corp_code"), str):
            raise MetadataIndexError("summary.companies의 세 기업 코드 매핑을 확인해 주세요.")
        code = matches[0]["corp_code"]
        if code in codes:
            raise MetadataIndexError("서로 다른 기업에 같은 corp_code를 사용할 수 없습니다.")
        codes.add(code)
        expected_counts[company.name] = sum(d.metadata["corp_code"] == code for d in documents)
    if (not all(expected_counts.values()) or sum(expected_counts.values()) != len(documents)
            or summary.get("company_counts") != expected_counts):
        raise MetadataIndexError("기업별 실제 건수와 summary.company_counts/기업코드 매핑이 일치하지 않습니다.")
    return documents


def input_report(documents: list[MetadataDocument], model: str) -> dict:
    counts = Counter(d.metadata["corp_code"] for d in documents)
    return {"collection": COLLECTION_NAME, "embedding_model": model,
            "document_count": len(documents), "unique_id_count": len({d.id for d in documents}),
            "company_counts": [{"corp_code": code,
                                "corp_name": next(d.metadata["corp_name"] for d in documents
                                                  if d.metadata["corp_code"] == code),
                                "count": count} for code, count in sorted(counts.items())]}


def open_metadata_collection(directory: Path, model: str):
    """No default model download or network embedding from Chroma is permitted."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    client = chromadb.PersistentClient(path=str(directory), settings=ChromaSettings(anonymized_telemetry=False))
    collection = client.get_or_create_collection(
        COLLECTION_NAME, embedding_function=None,
        metadata={"embedding_model": model, "schema_version": 1, "hnsw:space": "cosine"},
    )
    if (collection.metadata or {}).get("embedding_model") != model:
        raise MetadataIndexError("기존 컬렉션의 임베딩 모델이 다릅니다. 별도의 --db-dir를 사용해 주세요.")
    return collection


def collection_counts(collection, corp_codes: list[str], batch_size: int = 1000) -> dict:
    counts = {}
    for code in sorted(set(corp_codes)):
        offset, count = 0, 0
        while True:
            page = collection.get(where={"corp_code": code}, limit=batch_size, offset=offset, include=[])
            count += len(page["ids"])
            if len(page["ids"]) < batch_size:
                break
            offset += batch_size
        counts[code] = count
    return {"total": collection.count(), "by_corp_code": counts}


def index_metadata(documents: list[MetadataDocument], collection, embeddings: Embeddings,
                   *, model: str, batch_size: int = 64) -> dict:
    """Upsert stable IDs; skip unchanged documents; verify every input after writing.

    A failed batch does not roll back previous batches. Rerunning safely resumes.
    No deletion of records absent from this input is performed.
    """
    if not documents or not 1 <= batch_size <= 2048:
        raise MetadataIndexError("문서가 필요하며 batch-size는 1~2048이어야 합니다.")
    if len({d.id for d in documents}) != len(documents):
        raise MetadataIndexError("색인 입력 ID가 중복되었습니다.")
    if (collection.metadata or {}).get("embedding_model") != model:
        raise MetadataIndexError("컬렉션의 임베딩 모델이 다릅니다.")
    codes = [d.metadata["corp_code"] for d in documents]
    before = collection_counts(collection, codes)
    changed_count, skipped_count = 0, 0
    for start in range(0, len(documents), batch_size):
        batch = documents[start:start + batch_size]
        stored = collection.get(ids=[d.id for d in batch], include=["metadatas", "documents"])
        stored_by_id = {identifier: (metadata, text) for identifier, metadata, text in
                        zip(stored["ids"], stored["metadatas"], stored["documents"])}
        changed = [d for d in batch if stored_by_id.get(d.id) !=
                   ({**d.metadata, "content_hash": d.content_hash}, d.text)]
        skipped_count += len(batch) - len(changed)
        if not changed:
            continue
        try:
            vectors = embeddings.embed_documents([d.text for d in changed])
        except Exception:
            raise MetadataIndexError("임베딩 호출 실패: 키·모델·네트워크·한도를 확인해 주세요. "
                                     "일부 배치는 저장되었을 수 있으며 같은 명령으로 재시도할 수 있습니다.") from None
        if (len(vectors) != len(changed) or any(not vector or
                any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)
                for vector in vectors) or len({len(vector) for vector in vectors}) != 1):
            raise MetadataIndexError("임베딩 응답의 개수/차원/숫자 형식이 올바르지 않습니다.")
        collection.upsert(ids=[d.id for d in changed], documents=[d.text for d in changed],
                          metadatas=[{**d.metadata, "content_hash": d.content_hash} for d in changed],
                          embeddings=vectors)
        changed_count += len(changed)

    # Verify IDs, contents and company mappings independently of collection total.
    for start in range(0, len(documents), batch_size):
        batch = documents[start:start + batch_size]
        result = collection.get(ids=[d.id for d in batch], include=["documents", "metadatas"])
        actual = {identifier: (text, metadata) for identifier, text, metadata in
                  zip(result["ids"], result["documents"], result["metadatas"])}
        if any(actual.get(d.id) != (d.text, {**d.metadata, "content_hash": d.content_hash}) for d in batch):
            raise MetadataIndexError("색인 후 입력 ID/내용/메타데이터 검증에 실패했습니다.")
    after = collection_counts(collection, codes)
    return {"collection": COLLECTION_NAME, "embedding_model": model,
            "input_count": len(documents), "embedded_count": changed_count, "skipped_count": skipped_count,
            "before": before, "after": after, "count_delta": after["total"] - before["total"],
            "verified_input_count": len(documents)}
