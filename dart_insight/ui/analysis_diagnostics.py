"""Allowlisted analysis diagnostics; never serialize exception messages or payloads."""
import json
import re
from uuid import uuid4

from dart_insight.config import ConfigurationError
from dart_insight.opendart.client import DartError
from dart_insight.rag.llm import LLMError
from dart_insight.rag.query_extraction import QueryExtractionError
from dart_insight.vectorstore.metadata import MetadataIndexError

PHASES = {
    '기존 본문 색인 확인 중': 'index_check',
    '기존 본문 색인 재사용 중': 'index_reuse',
    '선택 공시 메타데이터 색인 중': 'metadata_index',
    '외부 문서 검색어 생성 중': 'query_extraction',
    '외부 출처 및 메타데이터에서 문서 후보 검색 중': 'candidate_search',
    'LLM으로 관련 후보 문서 선별 중': 'candidate_selection',
    '원문 다운로드 중': 'collect', '선별 공시 원문 수집 중': 'collect',
    'XML 처리 중': 'parse', '임베딩 및 색인 중': 'index',
    '관련 근거 검색 중': 'retrieve', 'GMS 답변 생성 중': 'generate',
    '근거 기반 답변 생성 중': 'generate', '이전 대화 확인 완료': 'conversation',
    '완료': 'complete',
}
CATEGORIES = {'authentication', 'not_found', 'request', 'rate_limit', 'server',
              'timeout', 'network', 'schema', 'citation', 'grounding', 'truncated',
              'empty_content', 'json_syntax', 'refusal', 'envelope', 'validator_internal', 'processing'}
DART_CODES = {'010','011','012','013','014','020','021','100','101','800','900','901',
              'timeout','network','http','response','company','bundle','client','unknown',
              'document_identity','document_empty','document_no_xml','document_size',
              'document_format','document_xml','document_zip','document_encoding',
              'document_control','document_entity','document_html'}
HINTS = {
    'original_unavailable': 'OpenDART에서 이 공시의 원문 파일을 제공하지 않습니다. 원문 링크에서 제공 여부를 확인해 주세요.',
    'original_empty': '원문에 분석 가능한 본문이 없어 분석하지 않았습니다.',
    'parse_failed': '원문을 정상적으로 읽지 못해 불완전한 본문으로 분석하지 않았습니다.',
    'identity_mismatch': '공시·기업 식별자가 일치하지 않아 중단했습니다. 기존 자료는 보존됩니다.',
    'authentication': '서비스 인증 설정을 확인해 주세요.',
    'rate_limit': '호출 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.',
    'transient_service': '외부 서비스의 일시적인 연결 또는 서버 오류입니다. 잠시 후 다시 시도해 주세요.',
    'gms_configuration': 'AI 모델 또는 요청 설정을 확인해 주세요.',
    'answer_validation': 'AI 응답의 형식 또는 근거 검증에 실패해 답변을 표시하지 않았습니다.',
    'index_configuration': '색인 또는 모델 설정을 확인해 주세요. 기존 자료는 보존됩니다.',
    'insufficient_evidence': '확인 가능한 답변 근거가 부족해 브리핑을 생성하지 않았습니다.',
    'previous_failure': '이전 실행이 실패했지만 해당 세션에 상세 원인이 남아 있지 않습니다.',
    'interrupted': '이전 분석이 완료되기 전에 중단되었습니다. 자동으로 다시 호출하지 않습니다.',
    'unexpected': '처리 실패: 원인을 확인할 수 없어 분석을 중단했습니다.',
}


class BriefingEvidenceError(ValueError):
    """An ungrounded/unknown answer is not a completed home briefing."""


def failure(exc, *, phase, origin, receipt, attempt=1, diagnostic_id=None):
    category = getattr(exc, 'category', 'processing')
    category = category if isinstance(category,str) and category in CATEGORIES else 'processing'
    code = getattr(exc, 'code', None)
    code = code if isinstance(code,str) and code in DART_CODES else None
    status = getattr(exc, 'status', None)
    status = status if type(status) is int and 100 <= status <= 599 else None
    reason = 'unexpected'
    if isinstance(exc, ConfigurationError) or category == 'authentication' or code in ('010','011','012','901'):
        reason = 'authentication'
    elif isinstance(exc, DartError):
        if code in ('013','014') and phase in ('collect','parse'):
            reason = 'original_unavailable'
        elif code == 'document_empty':
            reason = 'original_empty'
        elif code == 'document_identity':
            reason = 'identity_mismatch'
        elif code and code.startswith('document'):
            reason = 'parse_failed'
        elif code == '020':
            reason = 'rate_limit'
        elif code in ('timeout','network','800','900'):
            reason = 'transient_service'
    elif isinstance(exc, (LLMError, QueryExtractionError)):
        if category == 'rate_limit':
            reason = 'rate_limit'
        elif category in ('timeout','network','server'):
            reason = 'transient_service'
        elif category in ('not_found','request'):
            reason = 'gms_configuration'
        elif category in CATEGORIES - {'processing','authentication'}:
            reason = 'answer_validation'
    elif isinstance(exc, MetadataIndexError):
        reason = 'index_configuration'
    elif isinstance(exc, BriefingEvidenceError):
        reason = 'insufficient_evidence'
    kinds = (ConfigurationError,DartError,LLMError,QueryExtractionError,MetadataIndexError,BriefingEvidenceError,
             PermissionError,FileNotFoundError,TimeoutError,OSError,TypeError,KeyError,ValueError,RuntimeError)
    kind = next((cls.__name__ for cls in kinds if isinstance(exc,cls)), 'UnexpectedError')
    return {'event':'analysis_failed','diagnostic_id':diagnostic_id or uuid4().hex[:12],
            'receipt':receipt if re.fullmatch(r'\d{14}',str(receipt)) else 'invalid',
            'origin':origin if origin in ('home','detail','followup') else 'unknown',
            'phase':phase if phase in {*PHASES.values(),'start','runtime_close','render','session'} else 'processing',
            'attempt':attempt if type(attempt) is int and attempt > 0 else 1,
            'exception':kind,'category':category,'dart_code':code,'http_status':status,
            'reason':reason,'retryable':reason in ('transient_service','rate_limit')}


def emit(logger, diagnostic):
    logger.warning(json.dumps(diagnostic,ensure_ascii=True,sort_keys=True))


def home_retry_allowed(diagnostic, attempt_count):
    # No additional automatic RAG retry on top of transport's existing budget.
    # At most one deliberate home retry; rerenders never consume a new attempt.
    return bool(diagnostic and diagnostic.get('retryable') and attempt_count < 2)
