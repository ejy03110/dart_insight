"""Regression from user-provided Samsung XML lines, not a downloaded full filing."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

from defusedxml import ElementTree

from dart_insight.config import Settings
from dart_insight.data.collection_ledger import CollectionLedger
from dart_insight.data.documents import CollectedDocument, DocumentCandidate
from dart_insight.inspect_document import main
from dart_insight.opendart.client import DartError
from dart_insight.services.original_documents import OpenDartSource, parse_document_zip


TITLE = '<P>< TV 시장점유율 추이 ></P>'


def xml_fixture():
    lines = [''] * 6268
    lines[0] = '<DOCUMENT><P>'
    lines[6259] = '<SPAN USERMARK="0X000000">TV 시장 수요는 글로벌 경기 불확실성에 따른 소비심리 악화 영향으로 2022년 2억 </SPAN>'
    lines[6260] = '</P>'
    lines[6261] = '<P>328만대에서 2023년 2억 139만대 수준으로 소폭 감소하였습니다. 2024년은 파리올림픽 등 스포츠 이벤트에 따른 수요 확대로 전년비 3.7% 성장한 2억 883만대를 기록하였습니다(출처: Omdia 2025.2).</P>'
    lines[6262] = TITLE
    lines[6264] = '<TABLE AFIXTABLE="N" BORDER="1" WIDTH="795" ACLASS="NORMAL">'
    lines[6265] = '<TR><TD>시장점유율</TD><TD>테스트 전용 수치</TD></TR>'
    lines[6266] = '</TABLE>'
    lines[6267] = '</DOCUMENT>'
    return '\n'.join(lines)


def zipped(entries):
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, text in entries:
            archive.writestr(name, text.encode('utf-8'))
    return output.getvalue()


class LiteralTitleTests(unittest.TestCase):
    def test_reported_line_6263_column_4_is_reproduced_and_text_preserved(self):
        raw = xml_fixture()
        with self.assertRaises(ElementTree.ParseError) as caught:
            ElementTree.fromstring(raw)
        self.assertEqual(caught.exception.position, (6263, 4))
        diagnostics = []
        text = parse_document_zip(zipped([('1.xml', raw), ('2.xml', '<DOC>두 번째 XML</DOC>'),
                                          ('3.xml', '<DOC>세 번째 XML</DOC>')]), diagnostics=diagnostics)
        oracle = ElementTree.fromstring(raw.replace(TITLE, '<P>&lt; TV 시장점유율 추이 &gt;</P>'))
        expected = '\n'.join(t.strip() for t in oracle.itertext() if t.strip())
        self.assertEqual(text, expected + '\n\n두 번째 XML\n\n세 번째 XML')
        self.assertIn('< TV 시장점유율 추이 >', text)
        self.assertIn('2024년은 파리올림픽', text)
        self.assertIn('시장점유율\n테스트 전용 수치', text)
        self.assertIn('두 번째 XML', text)
        self.assertIn('세 번째 XML', text)
        self.assertEqual(len(diagnostics), 3)
        self.assertEqual(diagnostics[0]['literal_title_repairs'], 1)
        self.assertEqual(diagnostics[0]['repair_examples'], ['< TV 시장점유율 추이 >'])

    def test_each_xml_is_checked_and_independent_titles_are_repaired(self):
        diagnostics = []
        text = parse_document_zip(zipped([('1.xml', TITLE), ('2.xml', '<SPAN>< 지역별 매출 추이 ></SPAN>'),
                                          ('3.xml', '<P>세 번째 정상 XML</P>')]), diagnostics=diagnostics)
        self.assertEqual([d['literal_title_repairs'] for d in diagnostics], [1, 1, 0])
        self.assertIn('< 지역별 매출 추이 >', text)

    def test_unknown_damage_in_second_xml_is_not_silently_dropped_and_third_is_checked(self):
        diagnostics = []
        with self.assertRaises(DartError) as caught:
            parse_document_zip(zipped([('1.xml', TITLE), ('2.xml', '<DOC><P>bad</DOC>'),
                                        ('3.xml', '<DOC>세 번째</DOC>')]), diagnostics=diagnostics)
        self.assertEqual(len(diagnostics), 3)
        self.assertEqual(caught.exception.code, 'document_xml')
        self.assertIn('부분 추출 결과는 사용하지 않습니다', str(caught.exception))
        self.assertIn('XML #2', str(caught.exception))
        self.assertIn('text_characters', diagnostics[2])

    def test_valid_tags_cdata_comments_and_escaped_literals_do_not_change(self):
        text = parse_document_zip(zipped([('normal.xml', '<DOC><P>정상 <SPAN>태그</SPAN></P>'
            '<P>&lt; TV 시장점유율 추이 &gt;</P><![CDATA[< CDATA 제목 >]]><!-- < 주석 제목 > --></DOC>')]))
        self.assertEqual(text, '정상\n태그\n< TV 시장점유율 추이 >\n< CDATA 제목 >')
        for malformed in ('<P>< TABLE BORDER="1" ></P>', '<P>< /P ></P>', '<P><TABLE>표</P>', '<P><한글태그></P>'):
            with self.subTest(malformed=malformed), self.assertRaises(DartError):
                parse_document_zip(zipped([('bad.xml', malformed)]))

    def test_all_reported_patterns_across_three_xmls_preserve_complete_text(self):
        # Actual snippets supplied by the user; surrounding document is a fixture.
        bodies = [
            '<P>R&D 및 S&P</P><P>< TV 시장점유율 추이 ></P>',
            '<P><SPAN>앞부분</SPAN>< 디지털 콕핏 시장점유율 추이 ></P>',
            '<P><이사ㆍ감사 전체의 보수현황></P>',
        ]
        expected = ['R&D 및 S&P\n< TV 시장점유율 추이 >',
                    '앞부분\n< 디지털 콕핏 시장점유율 추이 >', '<이사ㆍ감사 전체의 보수현황>']
        diagnostics = []
        actual = parse_document_zip(zipped([
            (f'20250311001085{suffix}.xml', '<DOCUMENT>' + body + '</DOCUMENT>')
            for suffix, body in zip(('', '_00760', '_00761'), bodies)
        ]), diagnostics=diagnostics)
        self.assertEqual(actual, '\n\n'.join(expected))
        self.assertEqual([d['literal_title_repairs'] for d in diagnostics], [1, 1, 1])
        self.assertEqual(diagnostics[0]['normalizations']['bare_ampersands'], 2)
        self.assertTrue(all('error' not in d for d in diagnostics))

    def test_preserves_unicode_tags_attributes_entities_and_protected_sections(self):
        raw = ('<DOC><한글태그 X="R&D > S&P">정상</한글태그>'
               '<P>&amp; &lt; &gt; &quot; &apos; &#38; &#x26; &nbsp;</P>'
               '<![CDATA[R&D < 제목 >]]><!-- R&D < 제목 > -->'
               '<?instruction R&D?><P>앞 <제목 문구> 뒤</P></DOC>')
        diagnostics = []
        text = parse_document_zip(zipped([('a.xml', raw)]), diagnostics=diagnostics)
        self.assertEqual(text, '정상\n& < > " \' & &\nR&D < 제목 >\n앞 <제목 문구> 뒤')
        self.assertEqual(diagnostics[0]['normalizations']['bare_ampersands'], 2)

    def test_notes_survive_persistent_ledger_and_legacy_documents_still_load(self):
        candidate = DocumentCandidate('20250311001085', '20250311001085', '사업보고서 (2024.12)',
            'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250311001085', '00126380')
        with tempfile.TemporaryDirectory() as temp:
            client = Mock()
            client._request.return_value.content = zipped([('main.xml', TITLE), ('second.xml', '<P>R&D</P>')])
            doc = OpenDartSource(Settings(data_dir=Path(temp)), client).collect(candidate)
            self.assertIn('< TV 시장점유율 추이 >', doc.parse_notes[0])
            self.assertEqual(len(doc.parse_notes), 2)
            self.assertIn('앰퍼샌드 1건', doc.parse_notes[1])
            ledger = CollectionLedger(Path(temp) / 'ledger.sqlite3')
            ledger.ensure_pending(candidate.item_rcept_no, '제목')
            ledger.mark_collected(candidate.item_rcept_no, '제목', [doc])
            stored = CollectionLedger(ledger.path).get(candidate.item_rcept_no, '제목').documents[0]
            self.assertEqual(stored.parse_notes, doc.parse_notes)
            legacy = doc.to_dict()
            legacy.pop('parse_notes')
            legacy_doc = CollectedDocument(**legacy)
            self.assertEqual(legacy_doc.parse_notes, ())
            self.assertNotIn('parse_notes', legacy_doc.to_dict())

    def test_inspection_only_exports_after_all_xmls_pass_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'filing.zip'
            output = root / 'filing.txt'
            archive.write_bytes(zipped([('main.xml', TITLE)]))
            stdout = StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(['--input', str(archive), '--show-repairs', '--text-output', str(output)]), 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(output.read_text(encoding='utf-8'), '< TV 시장점유율 추이 >')
            self.assertEqual(report['xml_files_dropped'], 0)
            with redirect_stdout(StringIO()):
                self.assertEqual(main(['--input', str(archive), '--text-output', str(output)]), 1)
            self.assertEqual(output.read_text(encoding='utf-8'), '< TV 시장점유율 추이 >')
            archive.write_bytes(zipped([('first.xml', TITLE), ('second.xml', '<DOC>bad')]))
            with redirect_stdout(StringIO()):
                self.assertEqual(main(['--input', str(archive), '--text-output', str(root / 'partial.txt')]), 1)
            self.assertFalse((root / 'partial.txt').exists())
