"""Required workflow guardrails: readiness, bounded automatic attempts, evidence and charts."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from streamlit.testing.v1 import AppTest
import test_rag_ui as fixture
from dart_insight.config import Settings
from dart_insight.services.discovery import interpret_condition
from dart_insight.services.disclosure_search import SearchQuery
from dart_insight.services.runtime import analysis_readiness
from dart_insight.ui.analysis import ensure_analysis
from dart_insight.ui.charts import holdings_comparisons
from dart_insight.ui.evidence import public_excerpt, filing_date
from dart_insight.ui.explore import stored_matches


class GuardrailTests(unittest.TestCase):
    def test_failed_or_interrupted_attempt_never_implicitly_retries_and_success_is_reused(self):
        saved={};attempts={};run=Mock(return_value=None)
        ensure_analysis('a',saved,attempts,run)
        ensure_analysis('a',saved,attempts,run)
        run.assert_called_once()
        run.return_value={'answer':'확인된 분석','citations':[]}
        ensure_analysis('a',saved,attempts,run,retry=True)
        ensure_analysis('a',saved,attempts,run)
        self.assertEqual(run.call_count,2)
        self.assertEqual(saved['a']['content'],'확인된 분석')
        attempts['b']='running'
        ensure_analysis('b',saved,attempts,run)
        self.assertEqual(run.call_count,2)
        self.assertNotIn('b',saved)

    def test_readiness_requires_configuration_and_never_creates_a_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(data_dir=Path(directory),dart_api_key='test-only',openai_api_key='test-only')
            ready,reason=analysis_readiness(settings,fixture.FIRST)
            self.assertFalse(ready)
            self.assertIn('본문 미색인',reason)
            self.assertEqual(list(Path(directory).iterdir()),[])
            self.assertFalse(analysis_readiness(replace(settings,openai_api_key=''),fixture.FIRST)[0])

    def test_readiness_uses_actual_original_item_and_company_scope_in_existing_chroma(self):
        # A separate process releases Chroma handles before the temporary DB is removed.
        script='''
from pathlib import Path
import tempfile
import chromadb
from chromadb.config import Settings as C
from dart_insight.config import Settings
from dart_insight.services.runtime import analysis_readiness
from dart_insight.vectorstore.embeddings import PROFILE
from test_rag_ui import FIRST, SECOND
with tempfile.TemporaryDirectory() as tmp:
 s=Settings(db_dir=Path(tmp),dart_api_key='test-only',openai_api_key='test-only')
 client=chromadb.PersistentClient(path=tmp,settings=C(anonymized_telemetry=False))
 coll=client.create_collection('dart_documents',embedding_function=None,metadata={'embedding_model':s.embedding_model,'embedding_profile':PROFILE})
 coll.add(ids=['test'],documents=['테스트 본문'],embeddings=[[1.] + [0.]*1023],metadatas=[{'item_rcept_no':FIRST.rcept_no,'document_rcept_no':FIRST.rcept_no,'corp_code':FIRST.corp_code}])
 assert analysis_readiness(s,FIRST)[0]
 assert not analysis_readiness(s,SECOND)[0]
 assert coll.count()==1
'''
        result=subprocess.run([sys.executable,'-c',script],env={**__import__('os').environ,'PYTHONPATH':'tests:.'},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_explicit_negations_and_report_types_are_hard_filters(self):
        criteria=interpret_condition('삼성전자 사업보고서 제외하고 분기보고서 정정 제외')
        self.assertTrue(criteria.matches({'corp_name':'삼성전자','report_nm':'분기보고서 (2025.03)'}))
        for title in ('사업보고서 (2025.12)','[기재정정]분기보고서 (2025.03)','반기보고서'):
            self.assertFalse(criteria.matches({'corp_name':'삼성전자','report_nm':title}),title)
        self.assertFalse(interpret_condition('금양 말고 감사보고서').matches({'corp_name':'금양','report_nm':'감사보고서'}))
        self.assertFalse(interpret_condition('감사보고서만').matches({'report_nm':'사업보고서'}))
        self.assertTrue(interpret_condition('임원ㆍ주요주주특정증권등소유상황보고서만').matches({'report_nm':'임원·주요주주특정증권등소유상황보고서'}))
        self.assertFalse(interpret_condition('임원ㆍ주요주주특정증권등소유상황보고서 제외').matches({'report_nm':'임원·주요주주특정증권등소유상황보고서'}))

    def test_natural_candidates_are_scoped_before_top_k_so_form_filters_do_not_hide_valid_matches(self):
        from dart_insight.services.discovery import discover
        collection=Mock();collection.count.return_value=2
        collection.get.return_value={'ids':['meta:a','meta:b'],'documents':['A','B'],
                                     'metadatas':[fixture.FIRST.to_dict(),fixture.SECOND.to_dict()]}
        llm=Mock();llm.json.return_value=[fixture.SECOND.rcept_no]
        outside={'id':'meta:a','text':'A','metadata':fixture.FIRST.to_dict()}
        with patch('dart_insight.services.discovery.query_metadata',return_value=[outside]):
            result=discover(collection,'관련 공시',Mock(),Settings(candidate_top_k=1),llm,
                            receipt_scope={fixture.SECOND.rcept_no})
        self.assertEqual([r['metadata']['rcept_no'] for r in result],[fixture.SECOND.rcept_no])
        supplied=llm.json.call_args.args[1]['candidates']
        self.assertEqual([m['rcept_no'] for m in supplied],[fixture.SECOND.rcept_no])

    def test_stored_records_share_company_date_type_title_filters(self):
        q=SearchQuery('005930',date(2025,1,1),date(2025,12,31),'A','사업')
        self.assertTrue(stored_matches(fixture.FIRST,q))
        self.assertFalse(stored_matches(fixture.SECOND,q))
        self.assertFalse(stored_matches(replace(fixture.FIRST,corp_name='금양',stock_code='001570'),q))
        self.assertFalse(stored_matches(replace(fixture.FIRST,rcept_dt='2024-01-01'),q))

    def test_excerpt_preserves_financial_values_and_only_masks_display_identity_fields(self):
        original='생년월일\n1980-01-01\n보유 주식 수\n270\n비율(%)\n0.00\n연락처: 010-1234-5678\n보고의무발생일\n2025-12-24'
        result=public_excerpt(original)
        self.assertNotIn('1980-01-01',result)
        self.assertNotIn('010-1234-5678',result)
        for value in ('270','0.00','2025-12-24'):
            self.assertIn(value,result)
        self.assertIn('1980-01-01',original)
        self.assertEqual(filing_date(fixture.FIRST.source_url),'접수일 2025-01-01')
        self.assertEqual(filing_date('https://example.com/?rcpNo=20250101000001'),'접수일 미확인')

    def test_chart_uses_only_explicit_unit_labelled_unmerged_original_table(self):
        html='<table><tr><th>구분</th><th>보유 주식 수(주)</th></tr><tr><td>직전보고서</td><td>0</td></tr><tr><td>이번보고서</td><td>270</td></tr></table>'
        data=holdings_comparisons(html)
        self.assertEqual(data[0].values,(('직전보고서',0),('이번보고서',270)))
        self.assertEqual(data[0].unit,'주')
        self.assertFalse(holdings_comparisons(html.replace('(주)','(%)')))
        self.assertFalse(holdings_comparisons(html.replace('<th>구분','<th rowspan="2">구분')))
        self.assertFalse(holdings_comparisons(html.replace('270','확인불가')))
        self.assertFalse(holdings_comparisons('주권\n270\n0.00\n직전보고서\n0'))

    def test_horizontal_chart_shape_and_conflicting_duplicate_rows(self):
        html='<table><tr><th>구분</th><th>직전보고서</th><th>이번보고서</th></tr><tr><td>보유주식수(주)</td><td>1,000</td><td>1,270</td></tr></table>'
        self.assertEqual(holdings_comparisons(html)[0].values,(('직전보고서',1000),('이번보고서',1270)))
        self.assertFalse(holdings_comparisons(html.replace('1,270','1.270')))


class AutomaticUiTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def test_first_detail_automatically_analyzes_only_once_with_no_execution_button(self):
        app=fixture.start_analysis()
        self.service.ask.assert_called_once()
        self.assertFalse(any(b.key=='run_analysis' for b in app.button))
        app.run();app.run()
        self.service.ask.assert_called_once()
        fixture.open_disclosure(app,fixture.SECOND.rcept_no)
        self.assertEqual(self.service.ask.call_count,2)
        fixture.open_disclosure(app,fixture.FIRST.rcept_no)
        self.assertEqual(self.service.ask.call_count,2)
        self.assertEqual(len(app.chat_message),0)

    def test_failed_auto_analysis_has_explicit_retry_and_no_rerun_loop(self):
        self.service.ask.side_effect=RuntimeError('private-token-not-visible')
        app=fixture.start_analysis()
        self.service.ask.assert_called_once()
        self.assertTrue(any(b.key=='retry_analysis_'+fixture.FIRST.rcept_no for b in app.button))
        app.run();app.run()
        self.service.ask.assert_called_once()
        self.assertFalse(any('private-token-not-visible' in str(e.value) for e in app.error))
        self.service.ask.side_effect=self.answer
        app.button(key='retry_analysis_'+fixture.FIRST.rcept_no).click().run()
        self.assertEqual(self.service.ask.call_count,2)
        self.assertIn(fixture.FIRST.rcept_no,app.session_state['analyses'])
        app.run()
        self.assertEqual(self.service.ask.call_count,2)

    def test_metadata_only_blocks_auto_but_explicit_question_preserves_required_collection(self):
        with patch('dart_insight.services.runtime.analysis_readiness',return_value=(False,'본문 미색인 공시')):
            app=fixture.start_analysis()
            self.service.ask.assert_not_called()
            self.assertTrue(any('본문 미색인' in i.value for i in app.info))
            app.chat_input[0].set_value('이 공시 원문을 근거로 알려주세요').run()
            self.service.ask.assert_called_once()
            self.assertEqual(len(app.chat_message),2)

    def test_many_repeated_progress_events_remain_bounded_and_expose_current_failure(self):
        def failed(selected,question,progress,**kwargs):
            for _ in range(30):
                progress('LLM으로 관련 후보 문서 선별 중')
                progress('임베딩 및 색인 중')
            raise RuntimeError('private')
        self.service.ask.side_effect=failed
        app=fixture.start_analysis()
        text=' '.join(m.value for m in app.markdown)
        self.assertEqual(text.count('질문과 관련된 문서를 확인하고 있습니다.'),1)
        self.assertEqual(text.count('저장된 문서 색인을 확인하고 필요한 문서를 준비하고 있습니다.'),1)
        self.assertIn('실패 단계:',app.status[0].label)
        self.assertNotIn('답변을 작성',text)
        self.assertEqual(app.status[0].state,'error')

    def test_missing_keys_and_detail_disclaimer_are_visible_without_traceback(self):
        with patch('dart_insight.config.load_settings',return_value=Settings()),patch('dart_insight.services.runtime.analysis_readiness',return_value=(False,'AI 서비스 인증 설정이 없어 분석을 시작할 수 없습니다.')):
            app=fixture.start_analysis()
            self.assertTrue(any('투자 조언이 아닙니다' in c.value for c in app.caption))
            self.assertTrue(any('인증 설정' in i.value for i in app.info))
            self.assertEqual(len(app.chat_input),0)
            self.runtime.assert_not_called()


class UnifiedExplorationTests(unittest.TestCase):
    setUp=fixture.RagUiTests.setUp
    answer=fixture.RagUiTests.answer

    def query(self, app):
        app.text_input(key='api_company').set_value('005930')
        app.date_input(key='api_begin').set_value(date(2025,1,1))
        app.date_input(key='api_end').set_value(date(2025,12,31))
        app.selectbox(key='api_kind').select('A')
        app.button(key='api_submit').click().run()

    def test_api_and_stored_results_deduplicate_without_losing_server_page_totals(self):
        from dart_insight.services.disclosure_search import SearchPage
        api=Mock()
        api.search.return_value=SearchPage('success',(fixture.FIRST,),1,21,2,21)
        with patch('dart_insight.ui.search.search_service',return_value=api):
            app=AppTest.from_file(fixture.APP).run();fixture.explore(app);self.query(app)
            self.assertFalse(any(r.key=='_explore_scope' for r in app.radio))
            self.assertTrue(any('총 21건' in c.value for c in app.caption))
            self.assertTrue(any('저장 데이터 범위' in c.value for c in app.caption))
            keys=[b.key for b in app.button]
            self.assertIn('api_detail_'+fixture.FIRST.rcept_no,keys)
            self.assertNotIn('open_'+fixture.FIRST.rcept_no,keys)
            self.assertIn('open_'+fixture.SECOND.rcept_no,keys)
            self.service.ask.assert_not_called()

    def test_api_failure_and_no_results_are_distinct_and_saved_search_still_works(self):
        from dart_insight.services.disclosure_search import SearchPage
        api=Mock();api.search.return_value=SearchPage('error',message='공식 API 연결 실패')
        with patch('dart_insight.ui.search.search_service',return_value=api):
            app=AppTest.from_file(fixture.APP).run();fixture.explore(app);self.query(app)
            self.assertTrue(any('공식 API 연결 실패' in e.value for e in app.error))
            self.assertTrue(any(b.key=='open_'+fixture.FIRST.rcept_no for b in app.button))
            self.assertFalse(any('조회 조건에 해당하는 API 공시가 없습니다' in i.value for i in app.info))
            api.search.return_value=SearchPage('empty')
            app.run()
            self.assertFalse(any('공식 API 연결 실패' in e.value for e in app.error))
            self.assertTrue(any('조회 조건에 해당하는 API 공시가 없습니다' in i.value for i in app.info))

    def test_natural_condition_only_displays_selected_results_then_clear_restores_combined_list(self):
        with patch('dart_insight.services.discovery.discover',return_value=[{'metadata':fixture.SECOND.to_dict()}]):
            app=AppTest.from_file(fixture.APP).run();fixture.explore(app)
            app.text_area[0].set_value('사업보고서 제외 감사보고서만')
            app.button(key='FormSubmitter:discovery-조건에 맞는 공시 검색').click().run()
            self.assertTrue(any(b.key=='result_'+fixture.SECOND.rcept_no for b in app.button))
            self.assertFalse(any(b.key and b.key.startswith('open_') for b in app.button))
            app.button(key='result_'+fixture.SECOND.rcept_no).click().run()
            self.assertEqual(app.session_state['recent_detail'].rcept_no,fixture.SECOND.rcept_no)
            self.service.ask.assert_called_once()
            app.button(key='back_to_recent').click().run()
            app.button(key='clear_discovery').click().run()
            self.assertTrue(any(b.key=='open_'+fixture.FIRST.rcept_no for b in app.button))

    def test_unavailable_index_does_not_prevent_api_list_or_missing_index_notice(self):
        from dart_insight.services.disclosure_search import SearchPage
        api=Mock();api.search.return_value=SearchPage('success',(fixture.FIRST,),1,1,1,1)
        with patch('dart_insight.services.runtime.open_store',side_effect=RuntimeError('private-store-error')),patch('dart_insight.ui.search.search_service',return_value=api):
            app=AppTest.from_file(fixture.APP).run();fixture.explore(app);self.query(app)
            self.assertTrue(any(b.key=='api_detail_'+fixture.FIRST.rcept_no for b in app.button))
            self.assertTrue(any('미색인 공시' in c.value for c in app.caption))
            self.assertEqual(len(app.exception),0)

    def test_cold_url_preserves_stored_result_page(self):
        rows=[replace(fixture.FIRST,rcept_no=f'20250101{i:06d}') for i in range(1,26)]
        with patch('dart_insight.services.runtime.list_disclosures',return_value=rows):
            app=AppTest.from_file(fixture.APP)
            app.query_params.update(v='explore',scope='all',sp='2')
            app.run()
            self.assertEqual(app.selectbox(key='stored_page').value,2)
            self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('open_')]),5)
            self.assertEqual(len(app.exception),0)
