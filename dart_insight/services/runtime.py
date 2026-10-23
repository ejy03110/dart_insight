"""Production composition. Constructing the runtime does not contact LLM/model APIs."""
from dart_insight.data.collection_ledger import CollectionLedger
from dart_insight.data.models import Disclosure, COMPANIES
from dart_insight.rag.llm import GmsClient
from dart_insight.services.original_documents import BundleSource, OpenDartSource
from dart_insight.vectorstore.embeddings import create_embeddings
from dart_insight.vectorstore.metadata import open_metadata_collection, MetadataIndexError


def list_disclosures(collection):
    rows = []
    for offset in range(0, collection.count(), 1000):
        result = collection.get(limit=1000, offset=offset, include=['metadatas'])
        for metadata in result['metadatas']:
            stock = next((c.stock_code for c in COMPANIES if c.name == metadata['corp_name']), '')
            rows.append(Disclosure(
                corp_code=metadata['corp_code'], corp_name=metadata['corp_name'], stock_code=stock,
                report_nm=metadata['report_nm'], rcept_no=metadata['rcept_no'],
                rcept_dt=metadata['rcept_dt'], disclosure_type=metadata['disclosure_type'],
                source_url=metadata['source_url'], collected_at=metadata.get('collected_at', '')))
    return sorted(rows, key=lambda d: d.rcept_no, reverse=True)


def open_store(settings):
    try:
        metadata = open_metadata_collection(settings.chroma_dir, settings.embedding_model, create=False)
        if not metadata.count():
            raise MetadataIndexError('메타데이터 색인이 0건입니다. index_metadata 명령으로 먼저 색인하세요.')
        return metadata, create_embeddings(settings)
    except MetadataIndexError:
        raise
    except Exception:
        raise MetadataIndexError('메타데이터 컬렉션을 열 수 없습니다. DB 경로·색인·모델 설정을 확인하세요.') from None


class Runtime:
    def __init__(self, settings, metadata, embeddings):
        from dart_insight.services.rag_service import RagService
        from dart_insight.vectorstore.documents import open_document_collection
        self.settings = settings
        self.metadata = metadata if metadata is not None else open_metadata_collection(settings.chroma_dir, settings.embedding_model)
        self.embeddings = embeddings if embeddings is not None else create_embeddings(settings)
        self.llm = GmsClient(settings)
        self.source = BundleSource(settings.document_bundle) if settings.document_bundle else OpenDartSource(settings)
        self.service = RagService(settings, self.metadata,
                                  open_document_collection(settings.chroma_dir, settings.embedding_model),
                                  self.embeddings, CollectionLedger(settings.data_dir / 'collection.sqlite3'),
                                  self.source, self.llm)

    def __enter__(self):
        return self

    def ask(self, selected, question, progress=lambda _: None, *, history=None):
        from dart_insight.services.analysis_preparation import ensure_metadata
        ensure_metadata(selected, self.metadata, self.embeddings, self.settings, progress)
        return self.service.ask(selected, question, progress, history=history)

    def __exit__(self, *_):
        self.source.close()
        self.llm.close()


def analysis_readiness(settings, selected):
    """Read existing item AND original-document scope; never create a DB or model."""
    from pathlib import Path
    if not settings.openai_api_key:
        return False, 'AI 서비스 인증 설정이 없어 분석을 시작할 수 없습니다.'
    if not settings.dart_api_key and not settings.document_bundle:
        return False, '공시 원문 연결 설정이 없어 분석을 시작할 수 없습니다.'
    if not (Path(settings.chroma_dir) / 'chroma.sqlite3').is_file():
        return False, '본문 미색인 공시입니다. 원문 열람은 가능하며 AI 분석은 본문 색인 후 제공됩니다.'
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from dart_insight.vectorstore.metadata import check_collection_model
        client = chromadb.PersistentClient(path=str(settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False))
        collection = client.get_collection('dart_documents', embedding_function=None)
        check_collection_model(collection, settings.embedding_model)
        scope = {'$and': [{'item_rcept_no': selected.rcept_no},
                          {'document_rcept_no': selected.rcept_no},
                          {'corp_code': selected.corp_code}]}
        found = collection.get(where=scope, limit=1, include=['documents'])
        if found['ids'] and any(text.strip() for text in found['documents']):
            return True, 'AI 분석 가능 · 본문 색인 확인'
        return False, '본문 미색인 공시입니다. 원문 열람은 가능하며 AI 분석은 본문 색인 후 제공됩니다.'
    except Exception:
        return False, '본문 색인 상태를 확인할 수 없습니다. 기존 자료는 보존됩니다. 관리자에게 확인해 주세요.'
