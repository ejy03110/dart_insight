"""F2003: explicit GMS calls, validated queries, and mandatory receipt inclusion."""

import re
import time

import requests
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from dart_insight.config import ConfigurationError, Settings
from dart_insight.data.models import Disclosure
from dart_insight.rag.prompts.query_extraction import build_messages

GMS_CHAT_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/chat/completions"


class QueryExtractionError(ValueError):
    """Safe public error; never includes provider output or credentials."""


class QueryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    queries: list[StrictStr] = Field(min_length=1)

    @field_validator("queries")
    @classmethod
    def normalize(cls, queries):
        if any(not query.strip() for query in queries):
            raise ValueError("Empty query")
        return list(dict.fromkeys(query.strip() for query in queries))


def parse_queries(content: str) -> dict:
    """Validate the JSON schema locally, independent of provider JSON mode."""
    try:
        return QueryOutput.model_validate_json(content).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise QueryExtractionError("검색어 응답이 올바른 JSON 스키마가 아닙니다.") from None


def extract_queries(disclosure: Disclosure, question: str, settings: Settings, *, session=None) -> dict:
    """Return {queries: [...]}; at most 3 calls (initial + 2 parsing retries).

    settings.openai_api_key holds the GMS key, not a direct OpenAI credential.
    No calls occur on import. HTTP/auth/transport failures are not retried.
    """
    if not isinstance(disclosure, Disclosure) or not re.fullmatch(r"\d{14}", disclosure.rcept_no):
        raise QueryExtractionError("유효한 선택 공시와 14자리 접수번호가 필요합니다.")
    if not isinstance(question, str) or not question.strip():
        raise QueryExtractionError("사용자 질문이 필요합니다.")
    if not settings.openai_api_key.strip():
        raise ConfigurationError("GMS API 키를 OPENAI_API_KEY 설정에 입력해 주세요.")
    if not settings.chat_model.strip():
        raise ConfigurationError("OPENAI_CHAT_MODEL 설정이 필요합니다.")
    messages = build_messages(disclosure, question.strip())
    owned = session is None
    client = session if session is not None else requests.Session()
    try:
        for attempt in range(3):
            if attempt:
                time.sleep(settings.request_interval_seconds)
            try:
                response = client.post(
                    GMS_CHAT_URL,
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={"model": settings.chat_model, "messages": messages,
                          "response_format": {"type": "json_object"}},
                    timeout=settings.timeout_seconds,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise QueryExtractionError("GMS 검색어 추출 API 통신에 실패했습니다.") from None
            if response.status_code != 200:
                raise QueryExtractionError("GMS 검색어 추출 API 요청에 실패했습니다. 인증·모델·한도를 확인하세요.")
            try:
                content = response.json()["choices"][0]["message"]["content"]
                output = parse_queries(content)
            except (ValueError, KeyError, IndexError, TypeError):
                if attempt == 2:
                    raise QueryExtractionError("검색어 JSON 검증에 실패했습니다. 최대 2회 재시도를 마쳤습니다.") from None
                # Do not echo malformed provider text (possibly sensitive) into retries.
                messages = [*messages[:2], {"role": "user", "content":
                    '이전 출력의 JSON 검증에 실패했습니다. {"queries":["검색어"]}만 반환하세요.'}]
                continue
            # Keep the receipt independent of LLM compliance and semantic ranking.
            output["queries"] = list(dict.fromkeys([*output["queries"], disclosure.rcept_no]))
            return output
    finally:
        if owned:
            client.close()
