"""One item is one disclosure, identified by its receipt number."""

from dataclasses import asdict, dataclass
from datetime import date
import re


@dataclass(frozen=True)
class Company:
    name: str
    stock_code: str


COMPANIES = (
    Company("삼성전자", "005930"),
    Company("현대자동차", "005380"),
    Company("금양", "001570"),
)

# Official list API categories; report_nm is not a reliable category code.
DISCLOSURE_TYPES = {
    "A": "정기공시", "B": "주요사항보고", "C": "발행공시", "D": "지분공시",
    "E": "기타공시", "F": "외부감사관련", "G": "펀드공시",
    "H": "자산유동화", "I": "거래소공시", "J": "공정위공시",
}


@dataclass(frozen=True)
class Disclosure:
    corp_code: str
    corp_name: str
    stock_code: str
    report_nm: str
    rcept_no: str
    rcept_dt: str
    disclosure_type: str
    source_url: str
    collected_at: str
    source_type: str = "opendart_metadata"

    @classmethod
    def from_api(cls, row: dict, disclosure_type: str, collected_at: str):
        receipt = str(row["rcept_no"])
        corp_code = str(row["corp_code"])
        if not re.fullmatch(r"\d{14}", receipt) or not re.fullmatch(r"\d{8}", corp_code):
            raise ValueError("Invalid identifier")
        raw_date = str(row["rcept_dt"])
        parsed_date = date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}")
        return cls(
            corp_code=corp_code, corp_name=str(row["corp_name"]),
            stock_code=str(row.get("stock_code", "")), report_nm=str(row["report_nm"]),
            rcept_no=receipt, rcept_dt=parsed_date.isoformat(),
            disclosure_type=disclosure_type,
            source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            collected_at=collected_at,
        )

    def to_dict(self):
        return asdict(self)

    def to_index_text(self):
        return (
            f"기업명: {self.corp_name}\n종목코드: {self.stock_code}\n"
            f"공시명: {self.report_nm}\n접수번호: {self.rcept_no}\n"
            f"접수일: {self.rcept_dt}\n공시 유형: {self.disclosure_type}\n"
            f"원문 URL: {self.source_url}"
        )
