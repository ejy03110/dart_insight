"""Explicit, bounded GMS JSON calls. No network activity on import/startup."""
import json
import time

import requests

from dart_insight.config import ConfigurationError
from dart_insight.diagnostics import logger, measure
from dart_insight.rag.response import response_value, ResponseError
from dart_insight.rag.query_extraction import GMS_CHAT_URL, extract_queries


class LLMError(ValueError):
    """Curated public error; provider text is never included."""


class GmsClient:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or requests.Session()
        self.owned = session is None
        self.last_call = 0.0

    def close(self):
        if self.owned:
            self.session.close()

    def _wait(self):
        delay = self.settings.request_interval_seconds - (time.monotonic() - self.last_call)
        if delay > 0:
            time.sleep(delay)
        self.last_call = time.monotonic()

    def extract(self, selected, question):
        self._wait()
        try:
            return extract_queries(selected, question, self.settings, session=self.session)["queries"]
        finally:
            self.last_call = time.monotonic()

    def json(self, system, payload, validate):
        if not self.settings.openai_api_key:
            raise ConfigurationError("GMS API 키를 OPENAI_API_KEY에 설정해 주세요.")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(3):
            self._wait()
            try:
                with measure('gms_http', attempt=attempt + 1):
                    response = self.session.post(
                        GMS_CHAT_URL, headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                        json={"model": self.settings.chat_model, "messages": messages,
                              "response_format": {"type": "json_object"},
                              "max_completion_tokens": self.settings.gms_max_output_tokens},
                        timeout=self.settings.timeout_seconds, allow_redirects=False)
            except requests.RequestException:
                raise LLMError("GMS 연결 실패: 네트워크·시간 제한을 확인해 주세요.") from None
            try:
                logger.info('stage=gms_http http_status=%s attempt=%s', response.status_code, attempt + 1)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                if response.status_code != 200:
                    raise LLMError("GMS 요청 실패: 인증·모델 권한·호출 한도를 확인해 주세요.")
                try:
                    with measure('gms_validation', attempt=attempt + 1):
                        parsed = response_value(response)
                        try:
                            value = validate(parsed)
                        except (KeyError, IndexError, TypeError, ValueError):
                            raise ResponseError('schema_or_grounding') from None
                except ResponseError as exc:
                    logger.warning('stage=gms_validation status=%s category=%s attempt=%s',
                                   response.status_code, exc.category, attempt + 1)
                    retryable = exc.category in ('json_syntax', 'schema_or_grounding', 'empty_content')
                    if not retryable or attempt == 2:
                        guidance = {
                            'truncated': '출력 토큰 한도로 응답이 잘렸습니다. 질문 범위를 줄이거나 GMS_MAX_OUTPUT_TOKENS를 확인하세요.',
                            'refusal': '모델이 응답을 거절했습니다. 질문 내용을 확인하세요.',
                            'envelope': '모델 응답 구조가 예상과 다릅니다. 모델·게이트웨이 호환성을 확인하세요.',
                            'schema_or_grounding': 'JSON 필드 또는 근거 인용 검증에 실패했습니다.',
                        }.get(exc.category, '정상 JSON 답변을 받지 못했습니다. 모델 설정을 확인하세요.')
                        raise LLMError('GMS 응답 오류 [' + exc.category + ']: ' + guidance) from None
                    messages = [*messages[:2], {'role': 'user', 'content':
                        '이전 응답의 형식 또는 근거 검증에 실패했습니다. 시스템의 JSON 스키마와 근거 번호 규칙을 정확히 지켜 전체 JSON 객체만 반환하세요.'}]
                    continue
                return value
            finally:
                response.close()
        raise LLMError("GMS 요청을 완료하지 못했습니다.")
