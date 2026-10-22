"""F2004-2: local metadata candidates; no collection, LLM or indexing writes."""

from dataclasses import dataclass
import math

from dart_insight.data.documents import DocumentCandidate
from dart_insight.data.models import Disclosure
from dart_insight.vectorstore.metadata import (
    COLLECTION_NAME, Embeddings, check_collection_model, check_embedding_model, query_metadata,
)


class CandidateSearchError(ValueError):
    """Safe failure; never substitute an empty success for a failed search."""


@dataclass(frozen=True)
class QueryMatch:
    query: str
    distance: float

    @property
    def similarity(self) -> float:
        """Cosine similarity, not a calibrated relevance probability."""
        return 1.0 - self.distance


@dataclass(frozen=True)
class CandidateHit:
    candidate: DocumentCandidate
    corp_name: str
    matches: tuple[QueryMatch, ...]
    is_selected: bool

    @property
    def best_distance(self) -> float | None:
        return min((match.distance for match in self.matches), default=None)

    @property
    def best_similarity(self) -> float | None:
        distance = self.best_distance
        return None if distance is None else 1.0 - distance


@dataclass(frozen=True)
class CandidateSearchResult:
    candidates: tuple[CandidateHit, ...]
    searched_queries: tuple[str, ...]
    empty_queries: tuple[str, ...]
    matched_count: int

    @property
    def status(self) -> str:
        # Selected disclosure is still present when every similarity search is empty.
        return "ok" if self.matched_count else "empty"


def _candidate(selected: Disclosure, metadata: dict) -> tuple[DocumentCandidate, str]:
    name = metadata["corp_name"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Missing company name")
    if metadata["source_type"] != "opendart_metadata":
        raise ValueError("Unexpected source type")
    return DocumentCandidate(
        item_rcept_no=selected.rcept_no, document_rcept_no=metadata["rcept_no"],
        title=metadata["report_nm"], source_url=metadata["source_url"], corp_code=metadata["corp_code"],
    ), name


def search_candidates(collection, queries: list[str], selected: Disclosure, embeddings: Embeddings,
                      *, model: str, top_k: int = 5, corp_code: str | None = None) -> CandidateSearchResult:
    """Union per-query top_k, deduplicate by receipt, always include selected.

    Existing DocumentCandidate is composed, not extended or given invented fields.
    Company name and all query/distance evidence live in CandidateHit. Scores for
    a forced-only selected disclosure are None (no fabricated search evidence).
    Any query failure aborts the operation rather than returning partial success.
    """
    if not isinstance(queries, list) or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise CandidateSearchError("queries는 비어 있지 않은 검색어 문자열의 리스트여야 합니다.")
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise CandidateSearchError("top_k는 1~100 정수여야 합니다.")
    if not isinstance(selected, Disclosure):
        raise CandidateSearchError("선택 공시 Disclosure가 필요합니다.")
    if corp_code is not None and (not isinstance(corp_code, str) or len(corp_code) != 8
                                  or not corp_code.isascii() or not corp_code.isdigit()):
        raise CandidateSearchError("corp_code는 공식 8자리 기업코드여야 합니다.")
    searched = tuple(dict.fromkeys(q.strip() for q in queries))
    try:
        if collection.name != COLLECTION_NAME or (collection.metadata or {}).get("hnsw:space") != "cosine":
            raise ValueError("Metadata cosine collection required")
        check_collection_model(collection, model)
        check_embedding_model(embeddings, model)
        selected_candidate, selected_name = _candidate(selected, selected.to_dict())
        # Values are mutable only inside this operation; returned records are frozen.
        found = {selected.rcept_no: (selected_candidate, selected_name, [])}
        matched, empty = set(), []
        for query in searched:
            rows = query_metadata(collection, query, embeddings, model=model, top_k=top_k, corp_code=corp_code)
            if not rows:
                empty.append(query)
            for row in rows:
                candidate, name = _candidate(selected, row["metadata"])
                receipt = candidate.document_rcept_no
                if row["id"] != f"meta:{receipt}":
                    raise ValueError("Inconsistent candidate ID")
                distance = row["distance"]
                if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance):
                    raise ValueError("Invalid distance")
                if receipt in found:
                    old_candidate, old_name, matches = found[receipt]
                    if candidate != old_candidate or name != old_name:
                        raise ValueError("Conflicting metadata for one receipt")
                else:
                    matches = []
                    found[receipt] = candidate, name, matches
                matches.append(QueryMatch(query, float(distance)))
                matched.add(receipt)
        hits = [CandidateHit(candidate, name, tuple(matches), receipt == selected.rcept_no)
                for receipt, (candidate, name, matches) in found.items()]
        hits.sort(key=lambda hit: (not hit.is_selected,
                                  hit.best_distance if hit.best_distance is not None else math.inf,
                                  hit.candidate.document_rcept_no))
        return CandidateSearchResult(tuple(hits), searched, tuple(empty), len(matched))
    except Exception:
        raise CandidateSearchError("공시 후보 검색에 실패했습니다. DB·모델·메타데이터를 확인하세요.") from None
