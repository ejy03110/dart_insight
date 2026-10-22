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
        self.llm = GmsClient(settings)
        self.source = BundleSource(settings.document_bundle) if settings.document_bundle else OpenDartSource(settings)
        self.service = RagService(settings, metadata,
                                  open_document_collection(settings.chroma_dir, settings.embedding_model),
                                  embeddings, CollectionLedger(settings.data_dir / 'collection.sqlite3'),
                                  self.source, self.llm)

    def __enter__(self):
        return self.service

    def __exit__(self, *_):
        self.source.close()
        self.llm.close()
