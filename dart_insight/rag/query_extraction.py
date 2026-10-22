"""F2003: explicit GMS calls, validated queries, and mandatory receipt inclusion."""

import re
import time

import requests
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from dart_insight.config import ConfigurationError, Settings
from dart_insight.diagnostics import logger, measure
from dart_insight.rag.response import response_value, ResponseError
from dart_insight.data.models import Disclosure
from dart_insight.rag.prompts.query_extraction import build_messages

from dart_insight.rag.transport import GMS_CHAT_URL, GmsRequestError, post_chat


class QueryExtractionError(ValueError):
    """Safe public error; never includes provider output or credentials."""
    def __init__(self, message, *, category='validation', status=None):
        self.category, self.status = category, status
        super().__init__(message)


class QueryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    queries: list[StrictStr] = Field(min_length=1, max_length=6)

    @field_validator("queries")
    @classmethod
    def normalize(cls, queries):
        if any(not query.strip() or len(query) > 200 for query in queries):
            raise ValueError("Empty query")
        return list(dict.fromkeys(query.strip() for query in queries))


def parse_queries(content: str) -> dict:
    """Validate the JSON schema locally, independent of provider JSON mode."""
    try:
        return QueryOutput.model_validate_json(content).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise QueryExtractionError("검색어 응답이 올바른 JSON 스키마가 아닙니다.") from None


def extract_queries(disclosure: Disclosure, question: str, settings: Settings, *, session=None, history=None) -> dict:
    """Return {queries: [...]}; at most 3 calls total across transport and output retries.

    settings.openai_api_key holds the GMS key, not a direct OpenAI credential.
    No calls occur on import. Transient requests use bounded backoff; auth fails immediately.
    """
    if not isinstance(disclosure, Disclosure) or not re.fullmatch(r"\d{14}", disclosure.rcept_no):
        raise QueryExtractionError("유효한 선택 공시와 14자리 접수번호가 필요합니다.")
    if not isinstance(question, str) or not question.strip():
        raise QueryExtractionError("사용자 질문이 필요합니다.")
    if not settings.openai_api_key.strip():
        raise ConfigurationError("GMS API 키를 OPENAI_API_KEY 설정에 입력해 주세요.")
    if not settings.chat_model.strip():
        raise ConfigurationError("OPENAI_CHAT_MODEL 설정이 필요합니다.")
    messages = build_messages(disclosure, question.strip(), history)
    owned = session is None
    client = session if session is not None else requests.Session()
    try:
        for attempt in range(3):
            if attempt:
                time.sleep(settings.request_interval_seconds)
            try:
                response = post_chat(client, settings, messages, 'gms_query_http', attempt)
            except GmsRequestError as exc:
                if exc.retryable and attempt < 2:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise QueryExtractionError('GMS 검색어 추출 실패: ' + str(exc),
                                           category=exc.category, status=exc.status) from None
            try:
                with measure('gms_query_validation'):
                    output = QueryOutput.model_validate(response_value(response)).model_dump()
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                category = exc.category if isinstance(exc, ResponseError) else 'schema'
                logger.warning('stage=gms_query_validation category=%s http_status=%s attempt=%s', category, response.status_code, attempt + 1)
                if isinstance(exc, ResponseError) and category not in ('json_syntax', 'empty_content'):
                    raise QueryExtractionError('GMS 검색어 응답 오류 [' + category + ']: 모델 응답 또는 출력 한도를 확인하세요.', category=category) from None
                if attempt == 2:
                    raise QueryExtractionError("검색어 JSON 검증에 실패했습니다. 최대 2회 재시도를 마쳤습니다.", category=category) from None
                # Do not echo malformed provider text (possibly sensitive) into retries.
                messages = [*messages[:2], {"role": "user", "content":
                    '이전 출력의 JSON 검증에 실패했습니다. {"queries":["검색어"]}만 반환하세요.'}]
                continue
            finally:
                response.close()
            # Keep the receipt independent of LLM compliance and semantic ranking.
            output["queries"] = list(dict.fromkeys([*output["queries"], disclosure.rcept_no]))
            return output
    finally:
        if owned:
            client.close()
