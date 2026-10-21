"""F2006: pinned indexed metadata plus item-scoped semantic document retrieval."""
from dataclasses import asdict, dataclass
import math

from dart_insight.vectorstore.metadata import MetadataIndexError, check_collection_model, check_embedding_model
from dart_insight.vectorstore.embeddings import DIMENSION


@dataclass(frozen=True)
class Evidence:
    id: str
    text: str
    title: str
    source: str
    source_type: str

    def to_dict(self):
        return asdict(self)


def retrieve(metadata, documents, selected, question, embeddings, settings):
    for collection in (metadata, documents):
        check_collection_model(collection, settings.embedding_model)
    check_embedding_model(embeddings, settings.embedding_model)
    pinned = metadata.get(ids=[f'meta:{selected.rcept_no}'], include=['documents', 'metadatas'])
    if not pinned['ids']:
        raise MetadataIndexError('선택 공시의 메타데이터 색인이 없습니다. 먼저 메타데이터를 색인하세요.')
    m = pinned['metadatas'][0]
    if m['rcept_no'] != selected.rcept_no or m['corp_code'] != selected.corp_code:
        raise MetadataIndexError('선택 공시와 메타데이터 색인이 일치하지 않습니다.')
    evidence = [Evidence(pinned['ids'][0], pinned['documents'][0], m['report_nm'],
                         m['source_url'], 'opendart_metadata')]
    scope = {'item_rcept_no': selected.rcept_no}
    if settings.top_k == 1 or not documents.get(where=scope, limit=1, include=[])['ids']:
        return evidence
    vector = embeddings.embed_query(question)
    if len(vector) != DIMENSION or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise MetadataIndexError('질문 벡터 차원·숫자 형식이 올바르지 않습니다.')
    result = documents.query(query_embeddings=[vector], where=scope,
                             n_results=min(settings.top_k - 1, documents.count()),
                             include=['documents', 'metadatas'])
    seen_ids, seen_texts = {evidence[0].id}, set()
    for identifier, text, meta in zip(result['ids'][0], result['documents'][0], result['metadatas'][0]):
        if meta['item_rcept_no'] != selected.rcept_no:
            raise MetadataIndexError('다른 공시의 문서가 검색되었습니다. 색인을 확인해 주세요.')
        if identifier in seen_ids or text.strip() in seen_texts:
            continue
        seen_ids.add(identifier)
        seen_texts.add(text.strip())
        evidence.append(Evidence(identifier, text, meta['title'], meta['source_url'], 'opendart_document'))
    return evidence
