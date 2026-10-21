"""Explicit, bounded GMS JSON calls. No network activity on import/startup."""
import json
import time

import requests

from dart_insight.config import ConfigurationError
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
                response = self.session.post(
                    GMS_CHAT_URL, headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                    json={"model": self.settings.chat_model, "messages": messages,
                          "response_format": {"type": "json_object"}},
                    timeout=self.settings.timeout_seconds, allow_redirects=False)
            except requests.RequestException:
                raise LLMError("GMS 연결 실패: 네트워크·시간 제한을 확인해 주세요.") from None
            try:
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                if response.status_code != 200:
                    raise LLMError("GMS 요청 실패: 인증·모델 권한·호출 한도를 확인해 주세요.")
                try:
                    content = response.json()["choices"][0]["message"]["content"]
                    value = validate(json.loads(content))
                except (KeyError, IndexError, TypeError, ValueError):
                    if attempt == 2:
                        raise LLMError("GMS 응답 형식 검증에 실패했습니다. 잠시 후 다시 시도해 주세요.") from None
                    continue
                return value
            finally:
                response.close()
        raise LLMError("GMS 요청을 완료하지 못했습니다.")
