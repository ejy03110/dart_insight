"""Add only a clicked disclosure; verify full cached originals before reuse."""
from datetime import date
from dart_insight.services.original_documents import selected_candidate
from dart_insight.vectorstore.metadata import MetadataDocument, MetadataIndexError, index_metadata
from dart_insight.vectorstore.documents import document_chunks


def ensure_metadata(selected, collection, embeddings, settings, progress):
    selected_candidate(selected)  # Official URL and item/company identity validation.
    date.fromisoformat(selected.rcept_dt)
    existing = collection.get(ids=[f'meta:{selected.rcept_no}'], include=['metadatas', 'documents'])
    if existing['ids']:
        m = existing['metadatas'][0]
        if m.get('corp_code') != selected.corp_code or m.get('rcept_no') != selected.rcept_no:
            raise MetadataIndexError('선택 공시와 저장된 기업 식별자가 다릅니다. 기존 자료는 보존됩니다.')
        return  # Do not rewrite a previously indexed item's metadata.
    progress('선택 공시 메타데이터 색인 중')
    metadata = {key: value for key, value in selected.to_dict().items()
                if key in ('corp_code', 'corp_name', 'rcept_no', 'report_nm', 'rcept_dt',
                           'source_url', 'disclosure_type', 'source_type')}
    doc = MetadataDocument(f'meta:{selected.rcept_no}', selected.to_index_text(), metadata)
    index_metadata([doc], collection, embeddings, model=settings.embedding_model, batch_size=1)


def original_index_state(collection, ledger, selected, settings):
    """complete/missing/partial; never accept one surviving chunk as completion.

    Legacy chunks are verifiable against the full original already in the ledger.
    Missing provenance or incompatible chunks fail closed rather than resetting DBs.
    """
    scope = {'$and': [{'item_rcept_no': selected.rcept_no}, {'document_rcept_no': selected.rcept_no}]}
    stored = collection.get(where=scope, include=['documents', 'metadatas'])
    if not stored['ids']:
        return 'missing'
    doc = ledger.documents_for_item(selected.rcept_no).get(selected.rcept_no)
    if doc is None:
        raise MetadataIndexError('기존 본문 색인의 완료 여부를 검증할 원문 기록이 없습니다. 자료를 보존했으며 관리자 확인이 필요합니다.')
    if doc.corp_code != selected.corp_code or doc.item_rcept_no != selected.rcept_no:
        raise MetadataIndexError('저장 원문의 기업·공시 식별자가 다릅니다. 기존 자료는 보존됩니다.')
    expected = {chunk['id']: (chunk['text'], chunk['metadata']) for chunk in document_chunks(doc, settings)}
    actual = {i: (text, metadata) for i, text, metadata in zip(stored['ids'], stored['documents'], stored['metadatas'])}
    if any(i not in expected or expected[i] != value for i, value in actual.items()):
        raise MetadataIndexError('기존 본문 색인의 내용·청킹 설정이 다릅니다. 자동 재색인하지 않고 자료를 보존했습니다.')
    return 'complete' if len(actual) == len(expected) else 'partial'
