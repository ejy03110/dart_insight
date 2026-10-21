"""Environment > Streamlit Secrets > .env > defaults; keys never enter repr."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
DISCLAIMER = (
    "이 서비스는 공시 근거 기반 정보 제공 목적이며 투자 조언이 아닙니다. "
    "답변은 금융·투자 전문가의 조언을 대신하지 않습니다."
)


class ConfigurationError(ValueError):
    """A configuration error safe to show to the user."""


@dataclass(frozen=True)
class Settings:
    dart_api_key: str = field(default="", repr=False)
    openai_api_key: str = field(default="", repr=False)
    data_dir: Path = ROOT / ".local/data"
    timeout_seconds: float = 20
    request_interval_seconds: float = 0.3
    corp_cache_ttl_seconds: int = 86400
    list_cache_ttl_seconds: int = 900
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 5
    chat_model: str = "gpt-4.1-mini"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"
    embedding_cache_dir: Path | None = None

    def __post_init__(self):
        if not (0 < self.timeout_seconds <= 120):
            raise ConfigurationError("DART_TIMEOUT_SECONDS는 0 초과 120 이하여야 합니다.")
        if not (0 <= self.request_interval_seconds <= 60):
            raise ConfigurationError("호출 간격은 0~60초여야 합니다.")
        if min(self.corp_cache_ttl_seconds, self.list_cache_ttl_seconds) < 0:
            raise ConfigurationError("캐시 유효 시간은 0 이상이어야 합니다.")
        if not 0 <= self.chunk_overlap < self.chunk_size or self.top_k < 1:
            raise ConfigurationError("0 ≤ CHUNK_OVERLAP < CHUNK_SIZE, TOP_K ≥ 1이어야 합니다.")


def load_settings(secrets: Mapping | None = None) -> Settings:
    values = {**dotenv_values(ROOT / ".env"), **dict(secrets or {}), **os.environ}

    def get(name, default=""):
        value = values.get(name)
        return str(value).strip() if value is not None else str(default)

    path = Path(get("DART_DATA_DIR", ".local/data"))
    try:
        return Settings(
            dart_api_key=get("DART_API_KEY"),
            openai_api_key=get("OPENAI_API_KEY"),
            data_dir=path if path.is_absolute() else ROOT / path,
            timeout_seconds=float(get("DART_TIMEOUT_SECONDS", 20)),
            request_interval_seconds=float(get("DART_REQUEST_INTERVAL_SECONDS", 0.3)),
            corp_cache_ttl_seconds=int(get("DART_CORP_CACHE_TTL_SECONDS", 86400)),
            list_cache_ttl_seconds=int(get("DART_LIST_CACHE_TTL_SECONDS", 900)),
            chunk_size=int(get("CHUNK_SIZE", 1000)),
            chunk_overlap=int(get("CHUNK_OVERLAP", 200)),
            top_k=int(get("TOP_K", 5)),
            chat_model=get("OPENAI_CHAT_MODEL", "gpt-4.1-mini"),
            embedding_model=get("EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_device=get("EMBEDDING_DEVICE", "cpu"),
            embedding_cache_dir=(ROOT / get("EMBEDDING_CACHE_DIR")) if get("EMBEDDING_CACHE_DIR") else None,
        )
    except ConfigurationError:
        raise
    except ValueError:
        raise ConfigurationError("숫자형 환경 설정을 확인해 주세요.") from None
