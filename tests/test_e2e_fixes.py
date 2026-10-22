"""Q-01..Q-08 regression fixtures. Synthetic documents are never production fallbacks."""
from contextlib import redirect_stdout
from datetime import date
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZIP_DEFLATED, ZipFile

from dart_insight.config import Settings
from dart_insight.data.documents import DocumentCandidate
from dart_insight.data.models import Disclosure
from dart_insight.inspect_document import main as inspect_main
from dart_insight.opendart.client import DartError
from dart_insight.rag.pipeline import validate_answer
from dart_insight.rag.prompts.answer import ANSWER_PROMPT
from dart_insight.rag.retrieval import Evidence
from dart_insight.services.discovery import discover, empty_message, interpret_condition
from dart_insight.services.original_documents import OpenDartSource, parse_document_zip
from dart_insight.ui.chat import group_citations, separate_legacy_analysis


def archive(entries):
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as bundle:
        for name, body in entries:
            bundle.writestr(name, body)
    return output.getvalue()


class ReportXmlTests(unittest.TestCase):
    def test_declared_cp949_extended_korean_and_utf16_bom(self):
        for encoding, text in [('cp949', '똠방각하'), ('utf-16', '사업보고서 2024')]:
            raw = f'<?xml version="1.0" encoding="{encoding}"?><DOC>{text}</DOC>'.encode(encoding)
            diagnostics = []
            self.assertEqual(parse_document_zip(archive([('report.xml', raw)]), diagnostics=diagnostics), text)
            self.assertIn(diagnostics[0]['encoding'], ('cp949', 'utf-16'))

    def test_named_entities_bare_ampersands_dtd_and_cdata_are_handled_narrowly(self):
        raw = ('<!DOCTYPE DOCUMENT SYSTEM "https://invalid.invalid/dart.dtd">'
               '<DOCUMENT><TITLE>사업&nbsp;보고서</TITLE><P>R&D &cr; A&amp;B</P>'
               '<P><![CDATA[&nbsp; R&D <!ENTITY literal>]]></P></DOCUMENT>')
        with patch('requests.get', side_effect=AssertionError('No external DTD fetch')):
            diagnostics = []
            result = parse_document_zip(archive([('main.xml', raw.encode())]), diagnostics=diagnostics)
        self.assertIn('사업\u00a0보고서', result)
        self.assertIn('R&D', result)
        self.assertIn('A&B', result)
        self.assertIn('&nbsp; R&D <!ENTITY literal>', result)
        self.assertEqual(diagnostics[0]['normalizations'], {'named_entities': 2, 'bare_ampersands': 1})

    def test_multiple_xml_parts_including_large_tables_and_uppercase_extension(self):
        rows = '<ROW><CELL>테스트 전용 위험</CELL><CELL>123</CELL></ROW>' * 50000
        payload = archive([('part2.XML', '<DOC>추가 문서</DOC>'.encode()),
                           ('nested/part1.xml', ('<DOC>' + rows + '</DOC>').encode()),
                           ('image.bin', b'not xml')])
        diagnostics = []
        text = parse_document_zip(payload, diagnostics=diagnostics)
        self.assertEqual(text.count('테스트 전용 위험'), 50000)
        self.assertIn('추가 문서', text)
        self.assertEqual(len(diagnostics), 2)
        self.assertGreater(sum(d['uncompressed_bytes'] for d in diagnostics), 2_000_000)

    def test_limits_remain_enforced_for_expansion_and_file_count(self):
        payload = archive([('big.xml', b'<DOC>' + b'x' * 4096 + b'</DOC>')])
        with patch('dart_insight.services.original_documents.MAX_BYTES', 1024):
            with self.assertRaises(DartError) as caught:
                parse_document_zip(payload)
        self.assertEqual(caught.exception.code, 'document_size')
        self.assertIn('압축 해제', str(caught.exception))
        with self.assertRaises(DartError) as caught:
            parse_document_zip(archive([(f'{i}.xml', b'<DOC>x</DOC>') for i in range(1001)]))
        self.assertEqual(caught.exception.code, 'document_size')

    def test_unknown_entities_malformed_xml_empty_and_missing_xml_have_distinct_errors(self):
        cases = [(archive([('a.xml', b'<DOC>&unknown;</DOC>')]), 'document_xml'),
                 (archive([('a.xml', b'<DOC>\n<P>x</DOC>')]), 'document_xml'),
                 (archive([('a.xml', b'<DOC/>')]), 'document_empty'),
                 (archive([('a.txt', b'x')]), 'document_no_xml'),
                 (b'PKbadzip', 'document_zip')]
        for data, code in cases:
            with self.subTest(code=code), self.assertRaises(DartError) as caught:
                parse_document_zip(data)
            self.assertEqual(caught.exception.code, code)
            if code == 'document_xml':
                self.assertIn('행 ', str(caught.exception))
                self.assertNotIn('unknown', str(caught.exception))

    def test_entity_declarations_and_path_extraction_remain_forbidden(self):
        with self.assertRaises(DartError) as caught:
            parse_document_zip(archive([('bad.xml', b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><DOC>&e;</DOC>')]))
        self.assertEqual(caught.exception.code, 'document_entity')
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'should-not-exist.xml'
            self.assertEqual(parse_document_zip(archive([(str(target), b'<DOC>safe</DOC>')])), 'safe')
            self.assertFalse(target.exists())

    def test_failed_zip_is_retained_for_actual_local_diagnosis_without_overwrite(self):
        candidate = DocumentCandidate('20250101000001', '20250101000001', '사업보고서',
                                      'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000001', '00126380')
        with tempfile.TemporaryDirectory() as temp:
            source = OpenDartSource(Settings(data_dir=Path(temp)), Mock())
            response = source.client._request.return_value
            raw = archive([('annual.xml', b'<DOC>&unrecognized;</DOC>')])
            response.content = raw
            for _ in range(2):
                with self.assertRaises(DartError) as caught:
                    source.collect(candidate)
                self.assertIn('diagnostics/', str(caught.exception))
            files = list((Path(temp) / 'diagnostics').glob('*.zip'))
            self.assertEqual(len(files), 2)
            self.assertTrue(all(p.read_bytes() == raw for p in files))
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(inspect_main(['--input', str(files[0])]), 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report['code'], 'document_xml')
            self.assertIn('line', report['members'][0])
            self.assertNotIn('unrecognized', output.getvalue())


class YearSearchTests(unittest.TestCase):
    def setUp(self):
        self.annual = {'id': 'meta:1', 'text': '사업보고서', 'metadata': {
            'rcept_no': '20250311000001', 'corp_name': '삼성전자', 'corp_code': '00126380',
            'report_nm': '사업보고서 (2024.12)', 'rcept_dt': '2025-03-11'}}
        self.collection = Mock()
        self.collection.count.return_value = 1
        self.collection.get.return_value = {'ids': [self.annual['id']], 'documents': [self.annual['text']],
                                            'metadatas': [self.annual['metadata']]}
        self.llm = Mock()
        self.llm.json.side_effect = lambda system, payload, validate: validate({'selected_ids': [r['rcept_no'] for r in payload['candidates']]})

    @patch('dart_insight.services.discovery.query_metadata', return_value=[])
    def test_submission_year_matches_fiscal_2024_report_even_outside_vector_top_k(self, query):
        found = discover(self.collection, '2025년 삼성전자의 사업보고서를 찾아줘', Mock(), Settings(), self.llm)
        self.assertEqual([r['metadata']['rcept_no'] for r in found], ['20250311000001'])
        self.assertIn('제출연도', self.llm.json.call_args.args[1]['interpretation'])
        self.assertEqual(self.collection.get.call_args.kwargs['include'], ['documents', 'metadatas'])
        self.collection.upsert.assert_not_called()
        self.collection.delete.assert_not_called()

    @patch('dart_insight.services.discovery.query_metadata', return_value=[])
    def test_explicit_fiscal_year_and_no_year(self, query):
        for condition in ('삼성전자 2024 사업연도 사업보고서', '삼성전자 사업보고서 찾아줘'):
            self.assertEqual(len(discover(self.collection, condition, Mock(), Settings(), self.llm)), 1)
        self.assertTrue(interpret_condition('사업보고서 (2024.12)').fiscal)

    @patch('dart_insight.services.discovery.query_metadata', return_value=[])
    def test_unavailable_future_year_and_wrong_fiscal_year_do_not_call_selector(self, query):
        for condition in ('삼성전자 2035년 사업보고서', '삼성전자 2025 회계연도 사업보고서'):
            self.assertEqual(discover(self.collection, condition, Mock(), Settings(), self.llm), [])
        self.llm.json.assert_not_called()
        self.assertNotIn('본문 수치', empty_message('2035년 사업보고서'))

    def test_submission_vs_fiscal_and_numeric_condition_notice(self):
        metadata = self.annual['metadata']
        self.assertTrue(interpret_condition('2025년 사업보고서').matches(metadata))
        self.assertFalse(interpret_condition('2024년 사업보고서').matches(metadata))
        self.assertTrue(interpret_condition('2024 사업연도 사업보고서').matches(metadata))
        self.assertFalse(interpret_condition('금양 2025년 사업보고서').matches(metadata))
        self.assertIn('본문 수치', empty_message('매출 1조원 이상인 기업'))
        self.assertNotIn('본문 수치', empty_message('삼성전자 사업보고서'))
        self.assertIn('제출연도', interpret_condition('2025년 사업보고서').description)

    @patch('dart_insight.services.discovery.query_metadata')
    def test_numeric_body_conditions_are_not_invented_from_metadata(self, query):
        self.assertEqual(discover(self.collection, '매출 1조원 이상', Mock(), Settings(), self.llm), [])
        query.assert_not_called()
        self.llm.json.assert_not_called()


class GroundingAndSourcesTests(unittest.TestCase):
    def setUp(self):
        self.question = '이 공시에서 임원의 주식 취득 수량과 취득 원인을 알려줘.'
        self.evidence = [Evidence('doc:1', '변동수량 270주. 변동사유: 장내매수.', '소유상황보고서',
                                  'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250101000001', 'opendart_document')]

    def test_method_cannot_be_relabelled_as_purpose_or_missing_purpose_omitted(self):
        for answer in ('취득 목적: 장내매수 [근거 1]', '취득 수량 270주, 방식 장내매수 [근거 1]'):
            with self.assertRaises(ValueError):
                validate_answer({'answer': answer, 'supported': True}, self.evidence, self.question)
        answer = ('### 취득 내역\n- 취득 수량: 270주 [근거 1]\n- 취득 방식: 장내매수 [근거 1]\n'
                  '- 취득 목적은 제공된 문서에서 확인되지 않습니다.')
        self.assertEqual(validate_answer({'answer': answer, 'supported': True}, self.evidence, self.question)['answer'], answer)
        multiline = '### 취득 수량\n- 270주 [근거 1]\n### 취득 방식\n- 장내매수 [근거 1]\n### 취득 목적\n- 확인 불가'
        self.assertEqual(validate_answer({'answer': multiline, 'supported': True}, self.evidence, self.question)['answer'], multiline)
        self.assertIn('장내매수는 거래 방식', ANSWER_PROMPT)
        self.assertIn('Markdown', ANSWER_PROMPT)

    def test_explicit_documented_purpose_may_be_cited_and_unsupported_stays_unknown(self):
        evidence = [Evidence('doc:2', '취득 목적: 경영권 확보', '공시', 'https://example.invalid', 'opendart_document')]
        valid = {'answer': '취득 목적: 경영권 확보 [근거 1]', 'supported': True}
        self.assertEqual(validate_answer(valid, evidence, self.question)['citations'][0]['id'], 'doc:2')
        result = validate_answer({'answer': '추측', 'supported': False}, self.evidence, self.question)
        self.assertEqual(result['citations'], [])

    def test_same_url_has_one_link_but_keeps_all_cited_chunk_ids_numbers_and_types(self):
        citations = [{'number': i, 'id': f'doc:{i}', 'source': self.evidence[0].source,
                      'title': '사업보고서', 'source_type': 'opendart_document'} for i in (2, 3)]
        grouped = group_citations(citations)
        self.assertEqual(len(grouped), 1)
        self.assertEqual([c['number'] for c in grouped[0]['citations']], [2, 3])
        self.assertEqual([c['id'] for c in grouped[0]['citations']], ['doc:2', 'doc:3'])

    def test_previous_automatic_analysis_turns_migrate_without_removing_chat(self):
        histories = {'item': [{'role': 'user', 'content': 'analysis'},
                              {'role': 'assistant', 'content': '분석', 'citations': []},
                              {'role': 'user', 'content': '추가 질문'},
                              {'role': 'assistant', 'content': '추가 답변', 'citations': []}]}
        analyses = {}
        separate_legacy_analysis(histories, analyses, 'analysis')
        self.assertEqual(analyses['item']['content'], '분석')
        self.assertEqual(len(histories['item']), 2)
        separate_legacy_analysis(histories, analyses, 'analysis')
        self.assertEqual(len(histories['item']), 2)


class CandidateDiversityTests(unittest.TestCase):
    def test_comparison_search_can_supply_multiple_reports_without_forcing_a_quota(self):
        client = Mock()
        def reports(*args, **kwargs):
            if kwargs['disclosure_type'] != 'A':
                return []
            return [Disclosure('00126380', '삼성전자', '005930', f'사업보고서 ({year}.12)',
                               receipt, filed, '정기공시',
                               f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}', '')
                    for year, receipt, filed in [(2024, '20250311000001', '2025-03-11'),
                                                  (2023, '20240312000001', '2024-03-12')]]
        client.list_disclosures.side_effect = reports
        selected = reports(disclosure_type='A')[0]
        source = OpenDartSource(Settings(), client)
        candidates = source.search('삼성전자 사업보고서 기간별 비교', selected)
        self.assertEqual({c.document_rcept_no for c in candidates}, {'20250311000001', '20240312000001'})
        self.assertEqual(source.search('없는 문서유형', selected), [])
