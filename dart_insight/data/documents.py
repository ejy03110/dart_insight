"""F2004 document contracts. These models perform no collection or indexing."""

from dataclasses import asdict, dataclass
from datetime import datetime
import re


def validate_receipt(value: str):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{14}", value):
        raise ValueError("접수번호는 14자리 숫자여야 합니다.")


@dataclass(frozen=True)
class DocumentCandidate:
    item_rcept_no: str
    document_rcept_no: str
    title: str
    source_url: str
    corp_code: str
    source_type: str = "opendart_document"

    def __post_init__(self):
        validate_receipt(self.item_rcept_no)
        validate_receipt(self.document_rcept_no)
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("문서 제목이 필요합니다.")
        if not isinstance(self.corp_code, str) or not re.fullmatch(r"[0-9]{8}", self.corp_code):
            raise ValueError("공식 8자리 기업코드가 필요합니다.")
        # Persist the key-free viewer URL, never the authenticated download URL.
        expected = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.document_rcept_no}"
        if self.source_url != expected or self.source_type != "opendart_document":
            raise ValueError("키 없는 공식 공시 원문 URL과 출처 유형이 필요합니다.")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, kw_only=True)
class CollectedDocument(DocumentCandidate):
    text: str
    collected_at: str
    parse_notes: tuple[str, ...] = ()

    def to_dict(self):
        value = super().to_dict()
        if not self.parse_notes:
            # Retain the legacy JSON shape and idempotent ledger comparisons.
            value.pop('parse_notes', None)
        return value

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.parse_notes, (tuple, list)) or any(not isinstance(n, str) for n in self.parse_notes):
            raise ValueError("XML 보정 안내는 문자열 목록이어야 합니다.")
        object.__setattr__(self, 'parse_notes', tuple(self.parse_notes))
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("수집한 문서 본문이 필요합니다.")
        try:
            timestamp = datetime.fromisoformat(self.collected_at)
            if timestamp.utcoffset() is None:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("시간대가 포함된 ISO 수집 시각이 필요합니다.") from None
