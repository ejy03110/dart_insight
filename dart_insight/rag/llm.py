"""Explicit, bounded GMS JSON calls. No network activity on import/startup."""
import json
import time

import requests

from dart_insight.config import ConfigurationError
from dart_insight.diagnostics import logger, measure
from dart_insight.rag.response import response_value, ResponseError
from dart_insight.rag.validation import OutputValidationError
from dart_insight.rag.query_extraction import GMS_CHAT_URL, extract_queries, QueryExtractionError
from dart_insight.rag.transport import GmsRequestError, post_chat


class LLMError(ValueError):
    """Curated public error; provider text is never included."""


class GmsClient:
    def __init__(self, settings, session=None):
        self.settings = settings
        self.session = session or requests.Session()
        self.owned = session is None
        self.last_call = 0.0
        self._terminal_extraction_error = None

    def close(self):
        if self.owned:
            self.session.close()

    def _wait(self):
        delay = self.settings.request_interval_seconds - (time.monotonic() - self.last_call)
        if delay > 0:
            time.sleep(delay)
        self.last_call = time.monotonic()

    def extract(self, selected, question):
        self._terminal_extraction_error = None
        self._wait()
        try:
            return extract_queries(selected, question, self.settings, session=self.session)["queries"]
        except QueryExtractionError as exc:
            if exc.category in ('authentication', 'not_found', 'request'):
                self._terminal_extraction_error = str(exc)
            raise
        finally:
            self.last_call = time.monotonic()

    def json(self, system, payload, validate):
        operation = '최종 답변 생성' if 'evidence' in payload else '후보 선별'
        if self._terminal_extraction_error:
            raise LLMError('GMS ' + operation + ' 중단: 같은 요청의 검색어 추출에서 복구 불가능한 설정 오류가 확인되었습니다. ' + self._terminal_extraction_error)
        if not self.settings.openai_api_key:
            raise ConfigurationError("GMS API 키를 OPENAI_API_KEY에 설정해 주세요.")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        for attempt in range(3):
            self._wait()
            try:
                response = post_chat(self.session, self.settings, messages, 'gms_http', attempt)
            except GmsRequestError as exc:
                if exc.retryable and attempt < 2:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise LLMError('GMS ' + operation + ' 실패: ' + str(exc)) from None
            try:
                try:
                    with measure('gms_validation', attempt=attempt + 1):
                        parsed = response_value(response)
                        try:
                            value = validate(parsed)
                        except OutputValidationError as validation:
                            logger.warning(
                                'stage=gms_validator exception=%s category=%s field=%s rule=%s attempt=%s evidence_count=%s',
                                type(validation).__name__, validation.category, validation.field, validation.rule,
                                attempt + 1, len(payload.get('evidence', [])) if isinstance(payload.get('evidence'), list) else 0)
                            raise ResponseError(validation.category) from None
                        except (KeyError, IndexError, TypeError, ValueError) as validation:
                            logger.warning('stage=gms_validator exception=%s category=validator_internal field=$ rule=unclassified_validator attempt=%s',
                                           type(validation).__name__ if type(validation) in (KeyError, IndexError, TypeError, ValueError) else 'OtherValidationError', attempt + 1)
                            raise ResponseError('validator_internal') from None
                except ResponseError as exc:
                    logger.warning('stage=gms_validation status=%s category=%s attempt=%s',
                                   response.status_code, exc.category, attempt + 1)
                    retryable = exc.category in ('json_syntax', 'schema', 'citation', 'grounding', 'empty_content')
                    if not retryable or attempt == 2:
                        guidance = {
                            'truncated': '출력 토큰 한도로 응답이 잘렸습니다. 질문 범위를 줄이거나 GMS_MAX_OUTPUT_TOKENS를 확인하세요.',
                            'refusal': '모델이 응답을 거절했습니다. 질문 내용을 확인하세요.',
                            'envelope': '모델 응답 구조가 예상과 다릅니다. 모델·게이트웨이 호환성을 확인하세요.',
                            'schema': '필수 JSON 필드·자료형 검증에 실패했습니다. 서버 로그의 field/rule을 확인하세요.',
                            'citation': '실제 검색 근거와 인용 표기가 일치하지 않습니다. 서버 로그의 rule을 확인하세요.',
                            'grounding': '근거에 없는 취득 목적 생성 방지 규칙을 통과하지 못했습니다.',
                            'validator_internal': '검증기 내부 오류가 발생했습니다. 서버 로그의 exception/rule을 확인하세요.',
                        }.get(exc.category, '정상 JSON 답변을 받지 못했습니다. 모델 설정을 확인하세요.')
                        raise LLMError('GMS ' + operation + ' 응답 오류 [' + exc.category + ']: ' + guidance) from None
                    messages = [*messages[:2], {'role': 'user', 'content':
                        '이전 응답의 형식 또는 근거 검증에 실패했습니다. 시스템의 JSON 스키마와 근거 번호 규칙을 정확히 지켜 전체 JSON 객체만 반환하세요.'}]
                    continue
                return value
            finally:
                response.close()
        raise LLMError("GMS 요청을 완료하지 못했습니다.")
