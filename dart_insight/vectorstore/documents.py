"""F2005: stable item/document chunks, separate from metadata, BGE-only."""
import hashlib
import math

from langchain_text_splitters import RecursiveCharacterTextSplitter

from dart_insight.vectorstore.embeddings import DIMENSION, PROFILE
from dart_insight.vectorstore.metadata import check_collection_model, check_embedding_model, MetadataIndexError

COLLECTION_NAME = 'dart_documents'


def open_document_collection(directory, model):
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(directory), settings=Settings(anonymized_telemetry=False))
    collection = client.get_or_create_collection(
        COLLECTION_NAME, embedding_function=None,
        metadata={'embedding_model': model, 'embedding_profile': PROFILE,
                  'hnsw:space': 'cosine', 'schema_version': 1})
    check_collection_model(collection, model)
    return collection


def document_chunks(doc, settings):
    splitter = RecursiveCharacterTextSplitter(chunk_size=settings.chunk_size,
                                               chunk_overlap=settings.chunk_overlap,
                                               separators=['\n\n', '\n', '。', '. ', ' ', ''])
    texts = splitter.split_text(doc.text)
    if not texts:
        raise MetadataIndexError('수집 문서의 본문 청크가 없습니다.')
    fingerprint = hashlib.sha256(
        f'{settings.chunk_size}:{settings.chunk_overlap}:{doc.text}'.encode()).hexdigest()[:24]
    return [dict(id=f'doc:{doc.item_rcept_no}:{doc.document_rcept_no}:{fingerprint}:{i}', text=text,
                 metadata={'title': doc.title, 'source_url': doc.source_url,
                           'source_type': 'opendart_document', 'item_rcept_no': doc.item_rcept_no,
                           'document_rcept_no': doc.document_rcept_no, 'corp_code': doc.corp_code,
                           'chunk_index': i, 'chunk_size': settings.chunk_size,
                           'chunk_overlap': settings.chunk_overlap}) for i, text in enumerate(texts)]


def index_documents(documents, collection, embeddings, settings):
    check_collection_model(collection, settings.embedding_model)
    check_embedding_model(embeddings, settings.embedding_model)
    embedded = 0
    for doc in documents:
        chunks = document_chunks(doc, settings)
        scope = {'$and': [{'item_rcept_no': doc.item_rcept_no},
                          {'document_rcept_no': doc.document_rcept_no}]}
        previous = collection.get(where=scope, include=['documents', 'metadatas'])
        stored = {i: (t, m) for i, t, m in zip(previous['ids'], previous['documents'], previous['metadatas'])}
        changed = [c for c in chunks if stored.get(c['id']) != (c['text'], c['metadata'])]
        for start in range(0, len(changed), 16):
            batch = changed[start:start + 16]
            vectors = embeddings.embed_documents([c['text'] for c in batch])
            if len(vectors) != len(batch) or any(len(v) != DIMENSION or
                    any(not isinstance(x, (float, int)) or not math.isfinite(x) for x in v) for v in vectors):
                raise MetadataIndexError('문서 벡터의 개수·1024차원·숫자 형식을 확인해 주세요.')
            collection.upsert(ids=[c['id'] for c in batch], documents=[c['text'] for c in batch],
                              metadatas=[c['metadata'] for c in batch], embeddings=vectors)
            embedded += len(batch)
        actual = collection.get(ids=[c['id'] for c in chunks], include=['documents', 'metadatas'])
        verified = {i: (t, m) for i, t, m in zip(actual['ids'], actual['documents'], actual['metadatas'])}
        if any(verified.get(c['id']) != (c['text'], c['metadata']) for c in chunks):
            raise MetadataIndexError('문서 색인 후 검증에 실패했습니다. 같은 질문으로 재시도하세요.')
        # Rechunking replaces only this item's version of this document, after new chunks verify.
        stale = set(previous['ids']) - {c['id'] for c in chunks}
        if stale:
            collection.delete(ids=sorted(stale))
    return {'embedded_count': embedded, 'total': collection.count()}
