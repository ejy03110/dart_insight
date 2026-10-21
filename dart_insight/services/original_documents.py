"""OpenDART original ZIP/XML collection and an explicit offline document bundle."""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZipFile, BadZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from dart_insight.data.documents import CollectedDocument, DocumentCandidate
from dart_insight.data.models import DISCLOSURE_TYPES
from dart_insight.opendart.client import DartClient, DartError, check_status

MAX_BYTES = 100 * 1024 * 1024


def selected_candidate(selected):
    return DocumentCandidate(selected.rcept_no, selected.rcept_no, selected.report_nm,
                             selected.source_url, selected.corp_code)


def parse_document_zip(content):
    if not content.startswith(b'PK'):
        try:
            root = ElementTree.fromstring(content)
            check_status({"status": root.findtext("status")})
        except (ElementTree.ParseError, DefusedXmlException):
            raise DartError("공시 원문 응답이 ZIP/XML 형식이 아닙니다.", "response") from None
        raise DartError("공시 원문 ZIP 파일이 없습니다.", "response")
    try:
        with ZipFile(BytesIO(content)) as archive:
            files = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith('.xml')]
            if not files or len(files) > 1000 or sum(i.file_size for i in files) > MAX_BYTES:
                raise ValueError()
            parts = []
            for info in sorted(files, key=lambda i: i.filename):
                raw = archive.read(info)
                # Decode legacy Korean XML before parsing (Expat does not accept all encodings).
                decoded = None
                for encoding in ('utf-8-sig', 'euc-kr', 'cp949'):
                    try:
                        decoded = raw.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                if decoded is None:
                    raise ValueError()
                decoded = re.sub(r'^\s*<\?xml[^>]*\?>', '', decoded, count=1)
                root = ElementTree.fromstring(decoded)
                text = '\n'.join(t.strip() for t in root.itertext() if t.strip())
                if text:
                    parts.append(text)
            text = '\n\n'.join(parts)
            if not text.strip():
                raise ValueError()
            return text
    except (BadZipFile, KeyError, ValueError, RuntimeError, OSError, ElementTree.ParseError, DefusedXmlException):
        raise DartError("공시 원문 ZIP/XML 해석 실패: 손상·빈 본문·허용 크기를 확인해 주세요.", "response") from None


class OpenDartSource:
    """Search the actual external listing, then narrow with query terms.

    OpenDART has no free-text endpoint: fetch all types/pages for the selected
    company within the configured window and rank titles by lexical query overlap.
    Internal vector hits supplement, never replace, this external-source search.
    """
    def __init__(self, settings, client=None):
        self.client = client or DartClient(settings)
        self.owned = client is None
        self.settings = settings
        self.rows = None

    def close(self):
        if self.owned:
            self.client.close()

    def search(self, query, selected):
        if self.rows is None:
            end = date.fromisoformat(selected.rcept_dt)
            begin = end - timedelta(days=self.settings.document_search_days)
            rows = {}
            for category in DISCLOSURE_TYPES:
                for row in self.client.list_disclosures(selected.corp_code, begin, end,
                                                         disclosure_type=category, max_pages=None):
                    rows[row.rcept_no] = row
            self.rows = list(rows.values())
        terms = [t.casefold() for t in re.findall(r'[가-힣A-Za-z0-9]+', query) if len(t) >= 2]
        scored = []
        for row in self.rows:
            score = sum(t in row.report_nm.casefold() or t == row.rcept_no for t in terms)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1].rcept_no))
        return [DocumentCandidate(selected.rcept_no, row.rcept_no, row.report_nm,
                                  row.source_url, row.corp_code)
                for _, row in scored[:self.settings.candidate_top_k]]

    def collect(self, candidate):
        response = self.client._request('document.xml', {'rcept_no': candidate.document_rcept_no})
        try:
            content = response.content
            if len(content) > MAX_BYTES:
                raise DartError("공시 원문 다운로드 허용 크기를 초과했습니다.", "response")
            text = parse_document_zip(content)
        finally:
            response.close()
        return CollectedDocument(**candidate.to_dict(), text=text,
                                 collected_at=datetime.now(timezone.utc).isoformat())


class BundleSource:
    """Read actual OpenDART texts exported as JSON; never fabricate fallback bodies.

    schema_version=1, documents=[CollectedDocument.to_dict(), ...]. item_rcept_no
    is rebound to the selected item while original receipt/title/URL are retained.
    Only this document source is used in bundle mode (not a mixed-source feature).
    """
    def __init__(self, path):
        try:
            path = Path(path)
            if path.stat().st_size > MAX_BYTES:
                raise ValueError()
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if data.get('schema_version') != 1 or not isinstance(data.get('documents'), list):
                raise ValueError()
            docs = [CollectedDocument(**row) for row in data['documents']]
            if not docs or len({d.document_rcept_no for d in docs}) != len(docs):
                raise ValueError()
            self.documents = {d.document_rcept_no: d for d in docs}
        except (OSError, ValueError, TypeError, AttributeError):
            raise DartError("실제 공시 본문 문서 묶음의 JSON 형식·출처·경로를 확인해 주세요.", "bundle") from None

    def close(self):
        pass

    def search(self, query, selected):
        terms = re.findall(r'[가-힣A-Za-z0-9]+', query.casefold())
        return [DocumentCandidate(selected.rcept_no, doc.document_rcept_no, doc.title,
                                  doc.source_url, doc.corp_code)
                for doc in self.documents.values() if doc.corp_code == selected.corp_code and
                any(t in (doc.title + ' ' + doc.document_rcept_no).casefold() for t in terms)]

    def collect(self, candidate):
        doc = self.documents.get(candidate.document_rcept_no)
        if doc is None or doc.corp_code != candidate.corp_code:
            raise DartError("문서 묶음에 선택한 공시 원문이 없습니다. 실제 원문을 준비해 주세요.", "bundle")
        return CollectedDocument(**candidate.to_dict(), text=doc.text, collected_at=doc.collected_at)
