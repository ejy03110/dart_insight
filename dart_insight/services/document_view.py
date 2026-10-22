"""Explicit original-document reading, never RAG collection/indexing or file writes."""
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html import escape
from io import BytesIO
import logging
import re
from threading import RLock
import time
from zipfile import ZipFile, BadZipFile
import zlib

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from dart_insight.opendart.client import DartClient, DartError, check_status
from dart_insight.services.original_documents import MAX_BYTES, _decode_xml, _xml_text, selected_candidate

logger = logging.getLogger('dart_insight.operations')
CACHE_BYTES=32*1024*1024
CACHE_ENTRIES=20


@dataclass(frozen=True)
class DocumentView:
    state: str = 'unloaded'
    text: str = ''
    html: str = ''
    notes: tuple[str, ...] = ()
    checked_at: datetime | None = None


class UnsupportedDocument(ValueError):
    pass


def _structured(raw, diagnostic, text):
    """Only generated safe tags/attributes; source HTML/JS is never embedded."""
    try:
        root = ElementTree.fromstring(_decode_xml(raw)[0])
        local = lambda node: node.tag.rsplit('}',1)[-1].lower()
        is_html = diagnostic.get('input_format') == 'html'
        if is_html:
            root = next(node for node in root if local(node)=='body')
        visible=[]
        def render(node, depth=0):
            if depth>150:
                raise ValueError('deep_structure')
            name=local(node)
            if is_html and name in ('head','script','style'):
                return ''
            allowed={'table':{'caption','colgroup','col','thead','tbody','tfoot','tr'},
                     'thead':{'tr'},'tbody':{'tr'},'tfoot':{'tr'},'tr':{'td','th'}}
            if name in allowed and any(local(child) not in allowed[name] for child in node):
                raise ValueError('custom_table_structure')
            mapping={'title':'h3','subtitle':'h4','document-name':'h3','h1':'h3','h2':'h3','h3':'h3',
                     'h4':'h4','h5':'h4','h6':'h4','p':'p','table':'table','colgroup':'colgroup','col':'col','thead':'thead','tbody':'tbody',
                     'tfoot':'tfoot','tr':'tr','td':'td','th':'th','caption':'caption','br':'br'}
            tag=mapping.get(name,'div' if name in ('document','section','section-1','section-2','body') else 'span')
            attrs=''
            if tag in ('td','th'):
                attributes={key.lower():value for key,value in node.attrib.items()}
                for key in ('colspan','rowspan'):
                    if key in attributes:
                        value=attributes[key]
                        if not value.isascii() or not value.isdigit() or not 1<=int(value)<=1000:
                            raise ValueError('table_span')
                        attrs+=f' {key}="{int(value)}"'
            parts=[]
            if node.text:
                visible.append(node.text);parts.append(escape(node.text))
            for child in node:
                parts.append(render(child,depth+1))
                if child.tail:
                    visible.append(child.tail);parts.append(escape(child.tail))
            if tag in ('br','col'):
                return f'<{tag}>'+''.join(parts)
            return f'<{tag}{attrs}>'+''.join(parts)+f'</{tag}>'
        html=render(root)
        # Any structural rendering discrepancy uses the complete validated text instead.
        normalize=lambda value:re.sub(r'\s+','',value)
        if normalize(''.join(visible)) != normalize(text):
            return ''
        return html
    except (UnicodeError,ValueError,StopIteration,ElementTree.ParseError,DefusedXmlException,RecursionError):
        return ''


def parse_view(content):
    """ZIP XML/HTML members, or direct XML/HTML. All markup must parse safely."""
    if len(content)>MAX_BYTES:
        raise DartError('문서 크기 제한 초과','document_size')
    if not content.strip():
        raise DartError('제공된 본문이 없습니다.','document_empty')
    members=[];notes=[]
    if content.startswith(b'PK'):
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries=[info for info in archive.infolist() if not info.is_dir()]
                supported=[i for i in entries if i.filename.lower().endswith(('.xml','.html','.htm'))]
                if not supported:
                    raise UnsupportedDocument()
                if len(supported)>1000 or sum(i.file_size for i in supported)>MAX_BYTES:
                    raise DartError('문서 크기 제한 초과','document_size')
                total=0
                for info in sorted(supported,key=lambda i:i.filename):
                    with archive.open(info) as stream:
                        raw=stream.read(MAX_BYTES-total+1)
                    total+=len(raw)
                    if total>MAX_BYTES:
                        raise DartError('문서 크기 제한 초과','document_size')
                    members.append(raw)
                if len(supported)!=len(entries):
                    notes.append('첨부 이미지·PDF 등은 표시하지 않습니다. 첨부 자료는 공식 원문에서 확인하세요.')
        except (BadZipFile,RuntimeError,NotImplementedError,OSError,EOFError,zlib.error):
            raise DartError('문서 파일을 읽을 수 없습니다.','document_zip') from None
    else:
        try:
            decoded,_ = _decode_xml(content)
        except UnicodeError:
            raise UnsupportedDocument() from None
        if not decoded.lstrip('\ufeff \t\r\n').startswith('<'):
            raise UnsupportedDocument()
        members=[content]
    texts=[];fragments=[]
    for raw in members:
        diagnostic={}
        text=_xml_text(raw,diagnostic)
        # OpenDART may return a status XML instead of a document archive.
        try:
            root=ElementTree.fromstring(_decode_xml(raw)[0])
            if root.tag.lower()=='result' and root.find('status') is not None:
                check_status({'status':root.findtext('status')})
                raise DartError('제공된 본문이 없습니다.','document_empty')
        except (ElementTree.ParseError,DefusedXmlException):
            pass  # Narrow syntax repairs were already validated by the existing parser.
        if not text.strip():
            raise DartError('제공된 본문이 없습니다.','document_empty')
        texts.append(text)
        fragments.append(_structured(raw,diagnostic,text))
        if diagnostic.get('literal_title_repairs') or any(diagnostic.get('normalizations',{}).values()) or diagnostic.get('html_void_repairs'):
            notes.append('원문 의미를 보존하며 일부 문자·문서 형식을 정리했습니다. 공식 원문과 함께 확인하세요.')
    html='\n'.join(fragments) if all(fragments) else ''
    if not html:
        notes.append('이 문서는 표·문단 배치를 완전히 재현할 수 없어 전체 본문을 텍스트로 표시합니다.')
    return DocumentView('success','\n\n'.join(texts),html,tuple(dict.fromkeys(notes)))


class DocumentViewService:
    def __init__(self,settings,*,client_factory=None,timer=None):
        self.settings=replace(settings,request_interval_seconds=max(.3,settings.request_interval_seconds))
        self._factory=client_factory or DartClient
        self._timer=timer or time.monotonic
        self._cache=OrderedDict();self._lock=RLock()

    def peek(self,disclosure):
        selected_candidate(disclosure)  # Validate identity and the official key-free URL.
        with self._lock:
            key=(disclosure.corp_code,disclosure.rcept_no)
            if key in self._cache:
                self._cache.move_to_end(key)
            return self._cache.get(key,(DocumentView(),0,0))[0]

    def fetch(self,disclosure):
        candidate=selected_candidate(disclosure)
        key=(candidate.corp_code,candidate.document_rcept_no)
        with self._lock:
            previous=self._cache.get(key)
            ttl=3600 if previous and previous[0].state=='success' else 60
            if previous and self._timer()-previous[1]<ttl:
                return previous[0]
            if not self.settings.dart_api_key:
                result=DocumentView('error')
            else:
                try:
                    with self._factory(self.settings) as client:
                        response=client._request('document.xml',{'rcept_no':candidate.document_rcept_no})
                        try:
                            result=parse_view(response.content)
                        finally:
                            response.close()
                except UnsupportedDocument:
                    result=DocumentView('unsupported')
                except Exception as exc:
                    code=exc.code if isinstance(exc,DartError) else 'processing'
                    logger.warning('document_view_failed exception=%s',type(exc).__name__)
                    state='empty' if code=='document_empty' else 'unsupported' if code in ('014','document_format','document_no_xml') else 'error'
                    result=DocumentView(state)
            result=replace(result,checked_at=datetime.now(timezone.utc))
            size=len(result.text.encode())+len(result.html.encode())
            if size>CACHE_BYTES:
                result=DocumentView('unsupported',checked_at=result.checked_at)
                size=0
            self._cache.pop(key,None)
            while self._cache and (len(self._cache)>=CACHE_ENTRIES or sum(row[2] for row in self._cache.values())+size>CACHE_BYTES):
                self._cache.popitem(last=False)
            self._cache[key]=(result,self._timer(),size)
            return result
