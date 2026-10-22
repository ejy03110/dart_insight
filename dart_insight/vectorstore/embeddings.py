"""Lazy BGE-M3 dense embeddings shared by indexing and query search.

Importing/constructing the adapter does not import torch, download weights, or
contact an embedding API. Only encode() on first actual use loads the model.
"""

import math
from threading import RLock
from dart_insight.diagnostics import measure
from pathlib import Path

from dart_insight.config import ConfigurationError, Settings

LOCAL_MODEL = "BAAI/bge-m3"
DIMENSION = 1024
# Version the vector contract, including normalization/backend, not just its size.
PROFILE = "sentence-transformers:bge-m3:dense:1024:normalized:v1"


class LocalEmbeddingError(RuntimeError):
    """Sanitized local model errors."""


class LocalEmbeddings:
    def __init__(self, *, model_name=LOCAL_MODEL, device="cpu", cache_dir: Path | None = None,
                 batch_size=16):
        if model_name != LOCAL_MODEL:
            raise ConfigurationError("로컬 임베딩 모델은 BAAI/bge-m3를 사용해 주세요. EMBEDDING_MODEL을 확인하세요.")
        if not 1 <= batch_size <= 2048:
            raise ConfigurationError("임베딩 batch_size는 1~2048이어야 합니다.")
        self.model_name = model_name
        self.device = device
        self.cache_dir = cache_dir
        self.batch_size = batch_size
        self._model = None
        self._load_lock = RLock()

    def _load(self):
        with self._load_lock:
            return self._load_once()

    def _load_once(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                with measure('bge_model_load'):
                    self._model = SentenceTransformer(
                        self.model_name, device=self.device,
                        cache_folder=str(self.cache_dir) if self.cache_dir else None,
                        trust_remote_code=False,
                    )
            except Exception:
                raise LocalEmbeddingError("로컬 모델 로드 실패: 패키지 설치·모델 캐시·Hugging Face 접근·메모리를 확인하세요.") from None
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise LocalEmbeddingError("임베딩 텍스트는 비어 있지 않은 문자열이어야 합니다.")
        try:
            vectors = self._load().encode(
                texts, batch_size=self.batch_size, normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
            ).tolist()
        except LocalEmbeddingError:
            raise
        except Exception:
            raise LocalEmbeddingError("로컬 임베딩 계산 실패: 입력 길이·장치·메모리를 확인하고 batch-size를 줄여 보세요.") from None
        if (len(vectors) != len(texts) or any(len(vector) != DIMENSION or
                any(not math.isfinite(value) for value in vector) for vector in vectors)):
            raise LocalEmbeddingError("BAAI/bge-m3의 1024차원 dense 벡터 응답을 확인해 주세요.")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        # BGE-M3 uses the same encoder/normalization for documents and questions.
        return self.embed_documents([text])[0]


def create_embeddings(settings: Settings, *, batch_size=None) -> LocalEmbeddings:
    return LocalEmbeddings(
        model_name=settings.embedding_model, device=settings.embedding_device,
        cache_dir=settings.embedding_cache_dir or settings.data_dir / "models",
        batch_size=settings.embedding_batch_size if batch_size is None else batch_size,
    )
