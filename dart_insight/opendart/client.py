"""F2001 collection foundation: corporate codes, paginated metadata, safe errors."""

from datetime import date, datetime, timezone
from io import BytesIO
import re
import time
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dart_insight.config import ConfigurationError, Settings
from dart_insight.data.cache import JsonCache
from dart_insight.data.models import Company, DISCLOSURE_TYPES, Disclosure

BASE_URL = "https://opendart.fss.or.kr/api"
ERROR_MESSAGES = {
    "010": "등록되지 않은 OpenDART 인증키입니다.",
    "011": "사용할 수 없는 OpenDART 인증키입니다.",
    "012": "허용되지 않은 IP에서 요청했습니다. OpenDART 키 설정을 확인해 주세요.",
    "013": "조회된 공시가 없습니다.",
    "014": "해당 공시의 파일이 없습니다.",
    "020": "OpenDART 요청 한도를 초과했습니다. 호출을 중단하고 나중에 다시 시도해 주세요.",
    "021": "조회 가능한 기업 수를 초과했습니다.",
    "100": "OpenDART 요청 필드를 확인해 주세요.",
    "101": "OpenDART 접근이 제한되었습니다.",
    "800": "OpenDART 시스템 점검 중입니다.",
    "900": "OpenDART 서버 오류입니다. 나중에 다시 시도해 주세요.",
    "901": "OpenDART 인증키 사용 기간이 만료되었습니다.",
}


class DartError(RuntimeError):
    """Only curated text is exposed; raw requests errors can contain the key."""

    def __init__(self, message: str, code: str = "client"):
        super().__init__(message)
        self.code = code


def check_status(payload: dict) -> str:
    status = payload.get("status")
    if status not in ("000", "013"):
        raise DartError(ERROR_MESSAGES.get(status, "OpenDART 응답 상태를 확인할 수 없습니다."),
                        str(status) if status in ERROR_MESSAGES else "unknown")
    return status


class DartClient:
    def __init__(self, settings: Settings, session=None):
        if not settings.dart_api_key:
            raise ConfigurationError("DART_API_KEY를 환경변수 또는 Streamlit Secrets에 설정해 주세요.")
        self.settings = settings
        self.cache = JsonCache(settings.data_dir / "cache")
        self.session = session if session is not None else requests.Session()
        retries = Retry(total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=("GET",), raise_on_status=False)
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({"User-Agent": "DART-Insight/0.1 (read-only disclosure research)"})
        self._last_request = 0.0

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _request(self, endpoint: str, params: dict):
        delay = self.settings.request_interval_seconds - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()
        try:
            response = self.session.get(
                f"{BASE_URL}/{endpoint}",
                params={**params, "crtfc_key": self.settings.dart_api_key},
                timeout=self.settings.timeout_seconds,
                allow_redirects=False,
            )
            response.raise_for_status()
            if response.status_code != 200:
                raise DartError("OpenDART에서 예상하지 못한 HTTP 응답을 받았습니다.", "http")
            return response
        except requests.Timeout:
            raise DartError("OpenDART 연결 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", "timeout") from None
        except requests.RequestException:
            raise DartError("OpenDART 연결에 실패했습니다. 네트워크 허용 도메인과 연결 상태를 확인해 주세요.",
                            "network") from None

    def _json(self, endpoint: str, params: dict):
        try:
            payload = self._request(endpoint, params).json()
        except ValueError:
            raise DartError("OpenDART 응답을 JSON으로 해석할 수 없습니다.", "response") from None
        if not isinstance(payload, dict):
            raise DartError("OpenDART JSON 응답 형식이 올바르지 않습니다.", "response")
        check_status(payload)
        return payload

    def corporate_codes(self, *, refresh=False) -> list[dict]:
        """Download official corpCode.xml ZIP; never extract paths to disk."""
        cached = None if refresh else self.cache.get("corporations", {}, self.settings.corp_cache_ttl_seconds)
        if isinstance(cached, list) and cached:
            return cached
        response = self._request("corpCode.xml", {})
        content = response.content
        if not content.startswith(b"PK"):
            try:
                root = ElementTree.fromstring(content)
                check_status({"status": root.findtext("status")})
            except (ElementTree.ParseError, DefusedXmlException):
                raise DartError("OpenDART 기업코드 응답을 해석할 수 없습니다.", "response") from None
            raise DartError("OpenDART 기업코드 ZIP 파일이 없습니다.", "response")
        try:
            with ZipFile(BytesIO(content)) as archive:
                info = archive.getinfo("CORPCODE.xml")
                if info.file_size > 100 * 1024 * 1024:
                    raise DartError("기업코드 XML의 허용 크기를 초과했습니다.", "response")
                root = ElementTree.fromstring(archive.read(info))
            rows = [{"corp_code": row.findtext("corp_code", ""),
                     "corp_name": row.findtext("corp_name", ""),
                     "stock_code": row.findtext("stock_code", "").strip()}
                    for row in root.findall("list")]
            if not rows or any(not re.fullmatch(r"\d{8}", row["corp_code"]) for row in rows):
                raise ValueError("Invalid corporate codes")
        except (BadZipFile, KeyError, ValueError, ElementTree.ParseError, DefusedXmlException):
            raise DartError("OpenDART 기업코드 ZIP/XML 형식이 올바르지 않습니다.", "response") from None
        self.cache.put("corporations", {}, rows)
        return rows

    def resolve_company(self, company: Company, *, refresh=False) -> dict:
        matches = [row for row in self.corporate_codes(refresh=refresh)
                   if row["stock_code"] == company.stock_code]
        if len(matches) != 1:
            raise DartError("공식 기업코드에서 대상 기업을 고유하게 찾을 수 없습니다.", "company")
        return matches[0]

    def list_disclosures(self, corp_code: str, begin: date, end: date, *,
                         disclosure_type: str, max_pages: int | None = None,
                         refresh=False) -> list[Disclosure]:
        """Type is queried explicitly: list.json does not return pblntf_ty per row.

        This method returns a bounded sample if max_pages is specified. With None,
        retrieve every reported page; 013 means a genuine empty result, not failure.
        """
        if not re.fullmatch(r"\d{8}", corp_code):
            raise ConfigurationError("기업코드는 8자리 숫자여야 합니다.")
        if begin > end:
            raise ConfigurationError("조회 시작일은 종료일보다 늦을 수 없습니다.")
        if disclosure_type not in DISCLOSURE_TYPES:
            raise ConfigurationError("공시 유형은 A~J 중 하나여야 합니다.")
        if max_pages is not None and max_pages < 1:
            raise ConfigurationError("max_pages는 1 이상이어야 합니다.")
        items = {}
        page = 1
        while True:
            params = {"corp_code": corp_code, "bgn_de": begin.strftime("%Y%m%d"),
                      "end_de": end.strftime("%Y%m%d"), "pblntf_ty": disclosure_type,
                      "page_no": page, "page_count": 100, "sort": "date", "sort_mth": "desc"}
            # Success-only cache. Refresh is the explicit invalidation mechanism.
            envelope = None if refresh else self.cache.get("disclosures", params,
                                                           self.settings.list_cache_ttl_seconds)
            fetched = envelope is None
            if envelope is None:
                payload = self._json("list.json", params)
                envelope = {"payload": payload, "collected_at": datetime.now(timezone.utc).isoformat()}
            try:
                payload = envelope["payload"]
                status = check_status(payload)
                if status == "013":
                    if fetched:
                        self.cache.put("disclosures", params, envelope)
                    break
                rows = payload["list"]
                total_page = int(payload["total_page"])
                if not isinstance(rows, list) or not rows or total_page < page:
                    raise ValueError("Invalid pagination")
                records = [Disclosure.from_api(row, DISCLOSURE_TYPES[disclosure_type],
                                               envelope["collected_at"]) for row in rows]
                if any(item.corp_code != corp_code for item in records):
                    raise ValueError("Unexpected company")
            except (KeyError, ValueError, TypeError, AttributeError):
                raise DartError("OpenDART 공시 목록의 필수 필드 또는 페이지 정보가 올바르지 않습니다.",
                                "response") from None
            if fetched:
                self.cache.put("disclosures", params, envelope)
            items.update({item.rcept_no: item for item in records})
            if page >= total_page or (max_pages is not None and page >= max_pages):
                break
            page += 1
        return list(items.values())
