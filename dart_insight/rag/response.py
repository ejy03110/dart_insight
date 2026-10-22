"""Strict JSON envelope handling, with only lossless presentation normalization."""
import json
import re
from dart_insight.diagnostics import logger


class ResponseError(ValueError):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def response_value(response):
    try:
        envelope = response.json()
        choice = envelope['choices'][0]
        message = choice['message']
        if not isinstance(choice, dict) or not isinstance(message, dict):
            raise TypeError()
    except (ValueError, KeyError, IndexError, TypeError):
        raise ResponseError('envelope') from None
    reason = choice.get('finish_reason')
    usage = envelope.get('usage')
    usage = usage if isinstance(usage, dict) else {}
    safe_usage = {k: v for k, v in usage.items() if k in ('prompt_tokens', 'completion_tokens', 'total_tokens') and type(v) is int and v >= 0}
    logger.info('stage=gms_envelope finish=%s tokens=%s',
                reason if reason in (None, 'stop', 'length', 'content_filter', 'tool_calls') else 'other', safe_usage)
    if reason == 'length':
        raise ResponseError('truncated')
    if reason == 'content_filter' or message.get('refusal'):
        raise ResponseError('refusal')
    if reason not in (None, 'stop'):
        raise ResponseError('finish_reason')
    content = message.get('content')
    if isinstance(content, list):
        if not content or any(not isinstance(p, dict) or p.get('type') != 'text'
                              or not isinstance(p.get('text'), str) for p in content):
            raise ResponseError('content_type')
        content = ''.join(p['text'] for p in content)
    if not isinstance(content, str) or not content.strip():
        raise ResponseError('empty_content')
    content = content.strip().lstrip('\ufeff')
    fenced = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', content, re.I)
    if fenced:
        content = fenced[1]
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        return json.loads(content, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise ResponseError('json_syntax') from None
