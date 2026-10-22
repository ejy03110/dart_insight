"""Actual uploaded KRX-style HTML plus format/security regression matrix."""
from dataclasses import replace
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zipfile import ZipFile

from defusedxml import ElementTree
from dart_insight.config import Settings
from dart_insight.data.models import Disclosure, DISCLOSURE_TYPES
from dart_insight.opendart.client import DartError
from dart_insight.services.original_documents import OpenDartSource, parse_document_zip
from tests.test_q07_literal_titles import zipped

ACTUAL = Path(__file__).parent / 'fixtures' / 'opendart' / '20251203800556.zip'


class VisibleHTML(HTMLParser):
    """Independent standard-library oracle; no production normalization code."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = False
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.body = True
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden -= 1
        if tag == 'body':
            self.body = False

    def handle_data(self, text):
        if self.body and not self.hidden and text.strip():
            self.parts.append(text.strip())


class DocumentFormatTests(unittest.TestCase):
    def test_uploaded_actual_archive_reproduces_73_3_and_entire_visible_body_is_preserved(self):
        content = ACTUAL.read_bytes()
        self.assertEqual(sha256(content).hexdigest(), '78c132fa11294864063b190062d0c85c0da4b1f1c294635083c43492cc177f41')
        with ZipFile(ACTUAL) as archive:
            self.assertEqual(len(archive.infolist()), 1)
            raw = archive.read(archive.namelist()[0])
        with self.assertRaises(ElementTree.ParseError) as caught:
            ElementTree.fromstring(raw)
        self.assertEqual(caught.exception.position, (73, 3))
        diagnostics = []
        text = parse_document_zip(content, diagnostics=diagnostics)
        oracle = VisibleHTML(); oracle.feed(raw.decode('utf-8-sig')); oracle.close()
        self.assertEqual(text, '\n'.join(oracle.parts))
        self.assertEqual(len(text), 1505)
        self.assertIn('단기차입 실행일 변경', text)
        self.assertIn('SKAEEB TRADING & INVESTMENT CO., LTD.', text)
        self.assertIn('546,535,236,651원', text)
        self.assertNotIn('font-family', text)
        self.assertEqual(diagnostics[0]['html_void_repairs'], 14)
        self.assertEqual(diagnostics[0]['input_format'], 'html')

    def test_html_rules_are_format_scoped_and_preserve_xml_valid_elements(self):
        for raw, expected in [('<html><head><meta name="a" content="a>b"></head><body>앞<br>뒤</body></html>', '앞\n뒤'),
                              ('<html><body><br>실제 XML 본문</br></body></html>', '실제 XML 본문'),
                              ('<html xmlns="http://www.w3.org/1999/xhtml"><body>한글<br/>다음</body></html>', '한글\n다음'),
                              ('<DOC><meta>XML 자료</meta><br>본문</br></DOC>', 'XML 자료\n본문')]:
            with self.subTest(raw=raw):
                self.assertEqual(parse_document_zip(zipped([('a.xml', raw)])), expected)
        with self.assertRaises(DartError):
            parse_document_zip(zipped([('a.xml', '<DOC><meta>내용</DOC>')]))

    def test_broken_html_and_cdata_and_unknown_entities_fail_closed(self):
        for raw in ('<html><body><div>내용</body></html>', '<html><body>내용',
                    '<html><head><title>본문 없음</title></head></html>',
                    '<DOC><![CDATA[닫히지 않음</DOC>', '<DOC>&unknown;</DOC>',
                    '<DOC>&#0;</DOC>', '<html><body><p>내용 &unknown;</p></body></html>'):
            diagnostics = []
            with self.subTest(raw=raw), self.assertRaises(DartError):
                parse_document_zip(zipped([('1.xml', '<DOC>정상</DOC>'), ('2.xml', raw),
                                           ('3.xml', '<DOC>마지막 정상</DOC>')]), diagnostics=diagnostics)
            self.assertEqual(len(diagnostics), 3)
            self.assertIn('error', diagnostics[1])
            self.assertIn('text_characters', diagnostics[2])

    def test_illegal_characters_are_diagnosed_without_deleting_them(self):
        for char in ('\x00', '\x01', '\x0b', '\x0c', '\x1f', '\ufffe', '\uffff'):
            diagnostics = []
            with self.subTest(char=repr(char)), self.assertRaises(DartError) as caught:
                parse_document_zip(zipped([('a.xml', '<DOC>앞\n뒤' + char + '</DOC>')]), diagnostics=diagnostics)
            self.assertEqual(caught.exception.code, 'document_control')
            self.assertEqual(diagnostics[0]['line'], 2)
            self.assertIn('codepoint', diagnostics[0])
        self.assertEqual(parse_document_zip(zipped([('a.xml', '<DOC>한글\t정상\n\U0001f600</DOC>')])), '한글\t정상\n\U0001f600')

    def test_empty_xml_member_never_becomes_successful_partial_result(self):
        with self.assertRaises(DartError) as caught:
            parse_document_zip(zipped([('1.xml', '<DOC>정상</DOC>'), ('2.xml', '<DOC/>')]))
        self.assertEqual(caught.exception.code, 'document_empty')
        self.assertIn('부분 추출 결과는 사용하지 않습니다', str(caught.exception))

    def test_large_mixed_formats_nonxml_attachments_and_encoding(self):
        large = '보고서 본문 환율 위험.\n' * 30000
        from io import BytesIO
        with ZipFile(ACTUAL) as archive:
            html = archive.read(archive.namelist()[0])
        output = BytesIO()
        with ZipFile(output, 'w') as archive:
            archive.writestr('1.xml', ('<DOCUMENT>' + large + '</DOCUMENT>').encode('utf-16'))
            archive.writestr('2.xml', html)
            archive.writestr('3.xml', '<?xml version="1.0" encoding="euc-kr"?><DOC>짧은 공시</DOC>'.encode('euc-kr'))
            archive.writestr('../../ignored.pdf', b'not xml: \x00')
        diagnostics = []
        result = parse_document_zip(output.getvalue(), diagnostics=diagnostics)
        self.assertTrue(result.startswith(large.strip()))
        self.assertIn('단기차입 실행일 변경', result)
        self.assertTrue(result.endswith('짧은 공시'))
        self.assertEqual(len(diagnostics), 3)

    def test_deep_html_and_protected_void_closings_preserve_visible_text(self):
        raw = '<html><body><!-- </br> --><br>' + '<div>' * 1200 + '깊은 본문' + '</div>' * 1200 + '</body></html>'
        self.assertEqual(parse_document_zip(zipped([('a.xml', raw)])), '깊은 본문')
        raw = '<html><body>앞<style>css</style>중간<script>code</script>뒤</body></html>'
        self.assertEqual(parse_document_zip(zipped([('a.xml', raw)])), '앞\n중간\n뒤')

    def test_explicit_xml_encoding_is_not_silently_reinterpreted(self):
        from io import BytesIO
        output = BytesIO()
        with ZipFile(output, 'w') as archive:
            archive.writestr('a.xml', '<?xml version="1.0" encoding="utf-8"?><DOC>한글</DOC>'.encode('cp949'))
        with self.assertRaises(DartError) as caught:
            parse_document_zip(output.getvalue())
        self.assertEqual(caught.exception.code, 'document_encoding')

    def test_source_cache_tracks_company_and_date_window(self):
        one = Disclosure('00126380', '삼성전자', '005930', '사업보고서', '20250311001085',
                         '2025-03-11', '정기공시', 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250311001085', '')
        two = replace(one, corp_code='00591496', corp_name='현대자동차', stock_code='005380',
                      rcept_no='20250515000001', rcept_dt='2025-05-15', report_nm='분기보고서',
                      source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250515000001')
        client = Mock()
        def listing(code, begin, end, **kwargs):
            return [one if code == one.corp_code else two]
        client.list_disclosures.side_effect = listing
        with tempfile.TemporaryDirectory() as temp:
            source = OpenDartSource(Settings(data_dir=Path(temp)), client)
            self.assertEqual(source.search('사업보고서', one)[0].corp_code, one.corp_code)
            self.assertEqual(source.search('분기보고서', two)[0].corp_code, two.corp_code)
            source.search('다른 질문', two)
            self.assertEqual(client.list_disclosures.call_count, 2 * len(DISCLOSURE_TYPES))
            source.search('분기보고서', replace(two, rcept_dt='2025-06-15'))
            self.assertEqual(client.list_disclosures.call_count, 3 * len(DISCLOSURE_TYPES))
            client.list_disclosures.side_effect = lambda *args, **kwargs: [two]
            fresh = OpenDartSource(Settings(data_dir=Path(temp)), client)
            with self.assertRaises(DartError) as caught:
                fresh.search('사업보고서', one)
            self.assertEqual(caught.exception.code, 'document_identity')
