"""OpenDART original ZIP/XML collection and an explicit offline document bundle."""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import codecs
from html.entities import name2codepoint
import json
from pathlib import Path
import re
from uuid import uuid4
import zlib
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


def _decode_xml(raw):
    # Honor BOMs before testing ASCII-compatible declarations (UTF-16 often decoded
    # as UTF-8 with NULs by the previous implementation).
    for bom, encoding in ((b'\xff\xfe\x00\x00', 'utf-32'), (b'\x00\x00\xfe\xff', 'utf-32'),
                          (b'\xff\xfe', 'utf-16'), (b'\xfe\xff', 'utf-16')):
        if raw.startswith(bom):
            return raw.decode(encoding), encoding
    declaration = re.match(br'\s*<\?xml[^>]*encoding=[\"\']([^\"\']+)', raw[:300], re.I)
    encodings = ['utf-8-sig', 'euc-kr', 'cp949']
    if declaration:
        try:
            declared = codecs.lookup(declaration[1].decode('ascii')).name
        except (LookupError, UnicodeError):
            raise UnicodeError() from None
        if declared not in ('utf-8', 'euc_kr', 'cp949', 'utf-16', 'utf-16-le', 'utf-16-be',
                            'utf-32', 'utf-32-le', 'utf-32-be', 'ascii'):
            raise UnicodeError()
        encodings.insert(0, declared)
    for encoding in dict.fromkeys(encodings):
        try:
            return raw.decode(encoding), encoding
        except UnicodeError:
            continue
    raise UnicodeError()


def _xml_text(raw, diagnostic):
    decoded, encoding = _decode_xml(raw)
    diagnostic['encoding'] = encoding
    decoded = re.sub(r'^\ufeff?\s*<\?xml[^>]*\?>', '', decoded, count=1)
    # Reject entity declarations BEFORE normalization: never resolve local files,
    # remote DTDs, or an internal expansion payload. External DTD references alone
    # remain inert with defusedxml; they are never fetched.
    fragments = re.split(r'(<!\[CDATA\[.*?\]\]>|<!--.*?-->)', decoded, flags=re.S)
    if any(re.search(r'<!ENTITY\b', part, re.I) for part in fragments
           if not part.startswith(('<![CDATA[', '<!--'))):
        raise DartError('XML 엔티티 선언은 안전상 허용하지 않습니다.', 'document_entity')
    counts = {'named_entities': 0, 'bare_ampersands': 0}
    repairs = []

    def replace_entity(match):
        name = match[1]
        if name in ('amp', 'lt', 'gt', 'quot', 'apos'):
            return match[0]
        code = {'cr': 13, 'lf': 10}.get(name, name2codepoint.get(name))
        if code is None:
            return match[0]  # Unknown entities must still fail strict parsing.
        counts['named_entities'] += 1
        return f'&#{code};'

    # Lexical tokens protect quoted attribute values, comments, CDATA and PIs.
    # Valid tag-shaped tokens are never reinterpreted, including Unicode names.
    # The final strict parser remains responsible for nesting/namespaces/DTD syntax.
    name = r"[^\s<>&/='\"!?]+"
    attribute = name + r"\s*=\s*(?:\"[^\"]*\"|'[^']*')"
    valid_tag = re.compile(r"</" + name + r"\s*>|<" + name +
                           r"(?:\s+" + attribute + r")*\s*/?>")
    tokens = re.compile(
        r"<!\[CDATA\[.*?\]\]>|<!--.*?-->|<\?.*?\?>|"
        r"<!DOCTYPE(?:[^>\"'\[]|\"[^\"]*\"|'[^']*'|\[[\s\S]*?\])*>|"
        r"<(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>", re.S)

    def entities(part):
        part = re.sub(r'&([A-Za-z][A-Za-z0-9]*);', replace_entity, part)
        part, count = re.subn(
            r'&(?!\#(?:[0-9]+|x[0-9a-fA-F]+);|[A-Za-z_:][\w.:-]*;)', '&amp;', part)
        counts['bare_ampersands'] += count
        return part

    normalized, position = [], 0
    for match in tokens.finditer(decoded):
        normalized.append(entities(decoded[position:match.start()]))
        token = match[0]
        if token.startswith(('<!--', '<![CDATA[', '<?', '<!')):
            normalized.append(token)
        else:
            label = token[1:-1]
            # Only invalid opening tokens with Korean prose and no attribute or
            # closing-tag syntax qualify. Paired/custom tags stay structural;
            # malformed attributes and broken nesting must fail, never disappear.
            if (not valid_tag.fullmatch(token) and re.search(r'[가-힣]', label)
                    and not re.search(r"[<>=\"'/\r\n]", label)):
                repairs.append(label[:200])
                token = '&lt;' + label + '&gt;'
            normalized.append(entities(token))
        position = match.end()
    normalized.append(entities(decoded[position:]))
    decoded = ''.join(normalized)
    diagnostic['normalizations'] = counts
    diagnostic['literal_title_repairs'] = len(repairs)
    diagnostic['repair_examples'] = ['<' + label + '>' for label in repairs[:5]]
    root = ElementTree.fromstring(decoded)
    return '\n'.join(t.strip() for t in root.itertext() if t.strip())


def parse_document_zip(content, *, diagnostics=None):
    """Bounded in-memory ZIP reading and strict XML after narrow entity repair.

    diagnostics contains only member numbers, sizes, encoding, repair counts and
    curated errors plus short repaired-title examples. No full body or raw exceptions.
    """
    if len(content) > MAX_BYTES:
        raise DartError('공시 ZIP 다운로드 크기가 100 MiB 제한을 초과했습니다.', 'document_size')
    if not content.startswith(b'PK'):
        try:
            root = ElementTree.fromstring(content)
            check_status({"status": root.findtext("status")})
        except (ElementTree.ParseError, DefusedXmlException):
            raise DartError('공시 원문 응답이 ZIP 또는 OpenDART 상태 XML이 아닙니다.', 'document_format') from None
        raise DartError('공시 원문 ZIP 파일이 없습니다.', 'document_format')
    try:
        with ZipFile(BytesIO(content)) as archive:
            files = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith('.xml')]
            if not files:
                raise DartError('공시 ZIP에 XML 파일이 없습니다.', 'document_no_xml')
            if len(files) > 1000:
                raise DartError('공시 ZIP의 XML 파일 수가 1,000개 제한을 초과했습니다.', 'document_size')
            if sum(i.file_size for i in files) > MAX_BYTES:
                raise DartError('공시 ZIP의 XML 압축 해제 크기가 100 MiB 제한을 초과했습니다.', 'document_size')
            parts, total, failures = [], 0, []
            for number, info in enumerate(sorted(files, key=lambda i: i.filename), 1):
                diagnostic = {'xml_number': number, 'uncompressed_bytes': info.file_size}
                if diagnostics is not None:
                    diagnostics.append(diagnostic)
                # Never extract paths; enforce the actual streamed size as well as headers.
                with archive.open(info) as stream:
                    raw = stream.read(MAX_BYTES - total + 1)
                total += len(raw)
                if total > MAX_BYTES:
                    raise DartError('공시 XML의 실제 압축 해제 크기가 100 MiB 제한을 초과했습니다.', 'document_size')
                try:
                    text = _xml_text(raw, diagnostic)
                except UnicodeError:
                    diagnostic['error'] = 'unsupported_encoding'
                    failures.append(DartError(f'공시 XML #{number}: 문자 인코딩을 해석할 수 없습니다.', 'document_encoding'))
                    continue
                except (ElementTree.ParseError, DefusedXmlException) as exc:
                    line, column = getattr(exc, 'position', (0, 0))
                    kind = '정의되지 않은 XML 엔티티' if 'undefined entity' in str(exc) else 'XML 구문 오류'
                    diagnostic.update(error='xml_syntax', line=line, column=column)
                    failures.append(DartError(f'공시 XML #{number}: {kind} (행 {line}, 열 {column}). '
                                              '원문 ZIP을 보존해 로컬 진단 명령으로 확인해 주세요.', 'document_xml'))
                    continue
                except DartError as exc:
                    diagnostic['error'] = exc.code
                    failures.append(exc)
                    continue
                diagnostic['text_characters'] = len(text)
                if text:
                    parts.append(text)
            if failures:
                raise DartError(f'{len(files)}개 XML 검사 중 {len(failures)}개 오류. 부분 추출 결과는 사용하지 않습니다. '
                                f'{failures[0]}', failures[0].code)
            text = '\n\n'.join(parts)
            if not text.strip():
                raise DartError('공시 ZIP의 XML에 추출 가능한 본문이 없습니다.', 'document_empty')
            return text
    except DartError:
        raise
    except BadZipFile:
        raise DartError('공시 ZIP이 손상되었거나 CRC 검증에 실패했습니다.', 'document_zip') from None
    except (RuntimeError, NotImplementedError, OSError, EOFError, zlib.error):
        raise DartError('공시 ZIP 읽기 실패: 암호화·압축 방식·파일 손상을 확인해 주세요.', 'document_zip') from None


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
            try:
                diagnostics = []
                text = parse_document_zip(content, diagnostics=diagnostics)
            except DartError as exc:
                # Preserve only bounded ZIP bytes, never an authenticated URL or
                # a raw error response. Existing files/data are never replaced.
                if content.startswith(b'PK') and len(content) <= MAX_BYTES:
                    name = f'document-{candidate.document_rcept_no}-{uuid4().hex}.zip'
                    try:
                        directory = self.settings.data_dir / 'diagnostics'
                        directory.mkdir(parents=True, exist_ok=True)
                        with (directory / name).open('xb') as stream:
                            stream.write(content)
                    except OSError:
                        raise DartError(f'{exc} 진단 ZIP 저장에 실패했습니다. 데이터 경로 권한을 확인하세요.', exc.code) from None
                    raise DartError(f'{exc} 진단 ZIP: DART_DATA_DIR/diagnostics/{name}', exc.code) from None
                raise
        finally:
            response.close()
        return CollectedDocument(**candidate.to_dict(), text=text,
                                 collected_at=datetime.now(timezone.utc).isoformat(),
                                 parse_notes=tuple(
                                     f"XML #{d['xml_number']}: 제목형 꺾쇠 텍스트 {d['literal_title_repairs']}건 문자 보존 복구. "
                                     f"앰퍼샌드 {d['normalizations']['bare_ampersands']}건, "
                                     f"HTML 엔티티 {d['normalizations']['named_entities']}건 보정. "
                                     f"이 보정으로 삭제한 본문·생략한 XML 없음. 예: {', '.join(d['repair_examples'])}"
                                     for d in diagnostics if d.get('literal_title_repairs') or any(d.get('normalizations', {}).values())))


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
        return CollectedDocument(**candidate.to_dict(), text=doc.text, collected_at=doc.collected_at,
                                 parse_notes=doc.parse_notes)
