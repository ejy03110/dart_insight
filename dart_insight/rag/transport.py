"""One sanitized GMS request; callers own a shared three-attempt budget."""
import requests

from dart_insight.diagnostics import logger, measure

GMS_CHAT_URL = 'https://gms.ssafy.io/gmsapi/api.openai.com/v1/chat/completions'


class GmsRequestError(ValueError):
    def __init__(self, category, status=None, retryable=False, code=None, parameter=None):
        self.category, self.status, self.retryable = category, status, retryable
        guidance = {
            'authentication': '인증 실패: API 키와 모델 접근 권한을 확인하세요.',
            'not_found': '모델 또는 엔드포인트를 찾을 수 없습니다. 모델 이름·GMS 지원 경로를 확인하세요.',
            'rate_limit': '호출 한도를 초과했습니다. 잠시 후 다시 시도하세요.',
            'server': 'GMS 서버 오류입니다. 잠시 후 다시 시도하세요.',
            'timeout': 'GMS 응답 시간이 초과되었습니다. 네트워크·시간 제한을 확인하세요.',
            'network': 'GMS 네트워크 연결에 실패했습니다.',
            'request': '요청을 처리할 수 없습니다. 모델·지원 옵션을 확인하세요.',
        }[category]
        detail = (f' code={code}' if code else '') + (f' parameter={parameter}' if parameter else '')
        super().__init__(f'[{category}' + (f' HTTP {status}' if status is not None else '') + detail + '] ' + guidance)


def post_chat(session, settings, messages, stage, attempt):
    try:
        with measure(stage, attempt=attempt + 1):
            response = session.post(
                GMS_CHAT_URL, headers={'Authorization': f'Bearer {settings.openai_api_key}'},
                json={'model': settings.chat_model, 'messages': messages,
                      'response_format': {'type': 'json_object'},
                      'max_completion_tokens': settings.gms_max_output_tokens},
                timeout=settings.timeout_seconds, allow_redirects=False)
    except requests.RequestException as exc:
        category = 'timeout' if isinstance(exc, requests.Timeout) else 'network'
        # Log an allowlisted type, never the exception message/URL/headers.
        kind = ('Timeout' if isinstance(exc, requests.Timeout) else
                'ConnectionError' if isinstance(exc, requests.ConnectionError) else 'RequestException')
        logger.warning('stage=%s category=%s exception=%s attempt=%s', stage, category, kind, attempt + 1)
        raise GmsRequestError(category, retryable=True) from None
    status = response.status_code
    logger.info('stage=%s http_status=%s attempt=%s', stage, status, attempt + 1)
    if status == 200:
        return response
    # Only fixed provider codes and parameter names; never arbitrary body values.
    code = parameter = None
    try:
        body = response.json()
        error = body.get('error') if isinstance(body, dict) else None
        if isinstance(error, dict):
            candidate = error.get('code')
            if isinstance(candidate, str) and candidate in (
                    'invalid_api_key', 'model_not_found', 'insufficient_quota', 'rate_limit_exceeded',
                    'unsupported_parameter', 'invalid_request_error', 'permission_denied', 'context_length_exceeded'):
                code = candidate
            candidate = error.get('param')
            if isinstance(candidate, str) and candidate in ('model', 'response_format', 'max_completion_tokens', 'messages'):
                parameter = candidate
    except (ValueError, TypeError):
        pass
    finally:
        response.close()
    category = ('authentication' if status in (401, 403) else 'not_found' if status == 404 else
                'rate_limit' if status == 429 else 'server' if 500 <= status <= 599 else 'request')
    logger.warning('stage=%s http_status=%s category=%s code=%s parameter=%s attempt=%s', stage, status, category, code, parameter, attempt + 1)
    raise GmsRequestError(category, status, category in ('rate_limit', 'server'), code, parameter)
