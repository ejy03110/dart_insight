"""Synthetic original responses; never invoke RAG, embeddings or an operational DB."""
from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile
import unittest
from unittest.mock import Mock,MagicMock,patch

from dart_insight.config import Settings
from dart_insight.opendart.client import DartError
from dart_insight.services.document_view import DocumentViewService,parse_view,UnsupportedDocument
from test_rag_ui import FIRST


def archive(**files):
    stream=BytesIO()
    with ZipFile(stream,'w') as output:
        for name,body in files.items():
            output.writestr(name,body)
    return stream.getvalue()

XML='<DOCUMENT><TITLE>실제 형식 테스트 제목</TITLE><P>총액 1,234원. 비율 12.5%.</P><TABLE><TR><TH>항목</TH><TH>금액</TH></TR><TR><TD COLSPAN="1">운영자금</TD><TD>1,234</TD></TR></TABLE></DOCUMENT>'
HTML='<html><head><script>hidden_test_script()</script></head><body><h1>본문 제목</h1><p>총액 1,234원</p><table><tr><td>금액</td><td>1,234</td></tr></table><script>hidden_body_script()</script></body></html>'


class DocumentParserTests(unittest.TestCase):
    def test_xml_and_table_values_preserved(self):
        result=parse_view(XML.encode())
        self.assertEqual(result.state,'success')
        self.assertIn('<h3>실제 형식 테스트 제목</h3>',result.html)
        self.assertIn('<table>',result.html)
        self.assertIn('colspan="1"',result.html)
        self.assertIn('1,234원. 비율 12.5%.',result.text)
        self.assertIn('1,234',result.html)

    def test_html_direct_and_zip_members_and_scripts_not_executed(self):
        for content in [HTML.encode(),archive(**{'body.html':HTML,'other.xml':XML})]:
            result=parse_view(content)
            self.assertIn('총액 1,234원',result.text)
            self.assertNotIn('hidden_body_script',result.html)
            self.assertNotIn('hidden_test_script',result.text)
            self.assertNotIn('<script',result.html)

    def test_custom_table_layout_keeps_complete_text_instead_of_reparenting_cells(self):
        result=parse_view('<DOCUMENT><TABLE><TR><TE>항목</TE><TE>1,234</TE></TR></TABLE></DOCUMENT>'.encode())
        self.assertFalse(result.html)
        self.assertIn('항목',result.text)
        self.assertIn('1,234',result.text)

    def test_existing_narrow_repairs_fall_back_to_full_text(self):
        content='<html><body><p>금액&nbsp;1,234원<br>다음 문단</p></body></html>'
        result=parse_view(content.encode())
        self.assertEqual(result.state,'success')
        self.assertIn('1,234원',result.text)
        self.assertIn('다음 문단',result.text)
        self.assertFalse(result.html)
        self.assertTrue(result.notes)

    def test_unsupported_pdf_and_pdf_only_zip(self):
        for content in [b'%PDF-1.7 data',archive(**{'body.pdf':b'%PDF-1.7'})]:
            with self.assertRaises(UnsupportedDocument):
                parse_view(content)

    def test_empty_status_errors_and_malformed_partial_archive_fail(self):
        for content in [b'',b'<DOCUMENT/>']:
            with self.assertRaises(DartError) as caught:
                parse_view(content)
            self.assertEqual(caught.exception.code,'document_empty')
        with self.assertRaises(DartError) as caught:
            parse_view(b'<result><status>014</status><message>none</message></result>')
        self.assertEqual(caught.exception.code,'014')
        with self.assertRaises(Exception):
            parse_view(archive(**{'first.xml':XML,'second.xml':'<DOCUMENT>broken'}))

    def test_entities_and_untrusted_attributes_never_render(self):
        hostile='<html><body><p onclick="alert(1)">금액</p><img src="https://example.invalid/track"/><a href="javascript:alert(1)">링크 텍스트</a></body></html>'
        result=parse_view(hostile.encode())
        self.assertNotIn('onclick',result.html)
        self.assertNotIn('src=',result.html)
        self.assertNotIn('javascript:',result.html)
        self.assertIn('링크 텍스트',result.text)
        with self.assertRaises(DartError):
            parse_view(b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><DOCUMENT>&x;</DOCUMENT>')

    def test_unknown_attachments_are_explicitly_not_displayed_and_sizes_bounded(self):
        result=parse_view(archive(**{'body.xml':XML,'image.png':b'image'}))
        self.assertTrue(any('첨부' in note for note in result.notes))
        with patch('dart_insight.services.document_view.MAX_BYTES',10):
            with self.assertRaises(DartError):
                parse_view(XML.encode())


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.client=MagicMock();self.client.__enter__.return_value=self.client
        self.response=Mock(content=archive(**{'body.xml':XML}))
        self.client._request.return_value=self.response
        self.factory=Mock(return_value=self.client)
        self.tick=0
        self.service=DocumentViewService(Settings(dart_api_key='test-only'),client_factory=self.factory,timer=lambda:self.tick)

    def test_explicit_fetch_and_success_cache_expiry_no_file_writes(self):
        self.assertEqual(self.service.peek(FIRST).state,'unloaded')
        self.factory.assert_not_called()
        with patch('pathlib.Path.write_text',side_effect=AssertionError('No writes')),patch('pathlib.Path.write_bytes',side_effect=AssertionError('No writes')):
            result=self.service.fetch(FIRST)
        self.assertEqual(result.state,'success')
        self.client._request.assert_called_once_with('document.xml',{'rcept_no':FIRST.rcept_no})
        self.response.close.assert_called_once()
        self.tick=3599;self.service.fetch(FIRST)
        self.factory.assert_called_once()
        self.tick=3600
        self.service.peek(FIRST);self.factory.assert_called_once()
        self.service.fetch(FIRST)
        self.assertEqual(self.factory.call_count,2)

    def test_failure_backoff_and_no_raw_exception_leak(self):
        self.client._request.side_effect=RuntimeError('test-only-sensitive-message')
        result=self.service.fetch(FIRST)
        self.assertEqual(result.state,'error')
        self.assertNotIn('test-only-sensitive-message',str(result))
        self.tick=59;self.service.fetch(FIRST);self.factory.assert_called_once()
        self.tick=60;self.service.fetch(FIRST)
        self.assertEqual(self.factory.call_count,2)

    def test_missing_credentials_empty_and_unsupported_classification(self):
        empty=DocumentViewService(Settings(),client_factory=self.factory)
        self.assertEqual(empty.fetch(FIRST).state,'error');self.factory.assert_not_called()
        self.response.content=b''
        self.assertEqual(self.service.fetch(FIRST).state,'empty')
        self.tick=60;self.response.content=b'%PDF-1.7'
        self.assertEqual(self.service.fetch(FIRST).state,'unsupported')

    def test_canonical_url_required_and_cache_is_bounded(self):
        with self.assertRaises(ValueError):
            self.service.fetch(replace(FIRST,source_url='https://example.invalid'))
        self.factory.assert_not_called()
        with patch('dart_insight.services.document_view.CACHE_ENTRIES',1):
            self.service.fetch(FIRST)
            second=replace(FIRST,rcept_no='20250101000002',source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000002')
            self.service.fetch(second)
            self.assertEqual(self.service.peek(FIRST).state,'unloaded')
            self.assertEqual(self.service.peek(second).state,'success')
