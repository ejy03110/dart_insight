# F2001~F2012 최종 점검 (2026-10-22)

이 문서는 당시 검증 기록이다. 이번에 추가된 공식 PDF 직접 대조·최신 UX 및 NF2001~NF2007 판정은
[2026-10-23 최종 보고서](ux-quality-final.md)를 우선한다.

## 판정 기준과 증거

원본 `AI코어프로젝트_2회차_요구사항정의서.pdf`는 현재 저장소에 없다.
아래 요구는 기존 `requirements-analysis.md`의 p.11~15 추적표 및 `required-integration.md`를
대조한 **요지**이며 원문 직접 인용이 아니다. 원본과의 최종 일치 판정은 별도 확인이 필요하다.
초기 분석 문서의 '설계만/미구현'은 과거 기록이며 아래 현재 판정으로 대체한다.
F2013~F2016은 이번 범위에서 검토·구현하지 않는다.

증거는 구분한다.

- 자동화: 모의 OpenDART/GMS 응답, 실제 LangGraph, 임시 Chroma/SQLite, 모의 임베딩 및 실제 공개 ZIP 파서 회귀.
  테스트는 운영 DB/색인을 대상으로 수집·재색인하지 않는다.
- 사용자 실검증: 검색/상세/보관 공시/분석/추가 질문/연속 질문/공시별 분리/자연어 검색/빈 결과/복귀,
  근거 번호와 공식 원문 링크 정상. 답변 수치와 원문 일치 검증은 아직 수행하지 않았다는 보고.
- Codex 실 API: 인증키 미설정으로 OpenDART·GMS 실제 호출 미수행. BGE-M3 실제 모델 다운로드/재색인도 안 함.
  모의 HTTP 테스트 성공은 실제 API 성공으로 기록하지 않는다.
- 실제 공개 ZIP `tests/fixtures/opendart/20251203800556.zip`의 파서 회귀는 실제 파일 검증이지만,
  이번에 실제 GMS 답변을 생성하거나 답변 수치 정확성을 확인한 증거는 아니다.

## 요구별 구현·테스트·판정

아래 테스트 파일은 전체 회귀로 실행한다. 충족은 명시된 기능 계약 범위이며 원본 미대조와 실 API 한계를 공유한다.

| ID / 요구 요지 | 구현 위치·관련 함수 | 관련 테스트·결과 범위 | 구현/남은 검증·결함 | 판정 |
|---|---|---|---|---|
| F2001 / 실제 공시 100건 이상, 공시별 메타 텍스트 | `data/collection.py:collect_metadata,save_collection`, `data/models.py:Disclosure.to_index_text`, `opendart/client.py:DartClient` | `test_collection`, `test_opendart`: 페이지·기간 확장·중복·오류·실제 HTTP 경계 모의 통과 | 구현됨. 사용자 기존 1,065건 보고 있으나 현재 그 운영 파일의 고유 건수·텍스트 직접 확인 없음 | 부분 충족 |
| F2002 / BGE-M3 메타 색인·멱등 저장 | `vectorstore/metadata.py`, `vectorstore/embeddings.py`, `index_metadata.py` | `test_metadata_indexing:test_persisted_upsert_second_run_skips_all_and_company_filter_works`, `test_local_embeddings`: 임시 DB/모의1024벡터 통과 | 구현됨. 실제 운영 데이터·모델로 두 번 실행한 건수/품질 재검증 없음 | 부분 충족 |
| F2003 / LLM 검색어 JSON 파싱·예외·선택 접수번호 강제 포함 | `rag/query_extraction.py:extract_queries,parse_queries`, `rag/llm.py:GmsClient`, `rag/validation.py`, `services/rag_service.py:prepare` | `test_query_extraction`: 스키마/정규화/최대 재시도/인증/전송 오류; `test_gms_transport`, `test_gms_validation_details`: 모의 통과 | 구현됨. 파싱 실패 시 원래 질문+접수번호 fallback, 성공한 추출만 캐시. 실제 GMS 비정상 응답 재현 미검증 | 부분 충족 |
| F2004 / 후보 검색·선별·실제 원문 수집·재사용 | `services/original_documents.py:OpenDartSource,selected_candidate`, `services/rag_service.py:prepare`, `data/collection_ledger.py:CollectionLedger` | `test_rag_pipeline:test_external_search_uses_all_types_and_no_page_limit_then_downloads_receipt`, `test_document_ledger`, `test_pipeline_matrix`: 선택 원문 강제·실패 재개·재사용 모의 통과 | 구현됨. SQLite 원장은 질문/후보/수집 문서용. 실제 API에서 재질문 외부 호출0 계측은 미검증 | 부분 충족 |
| F2005 / 청킹·별도 문서 컬렉션·관련 항목 격리 | `vectorstore/documents.py:document_chunks,index_documents,open_document_collection` | `test_rag_pipeline:test_real_db_graph_retry_isolation_and_rechunking_in_child`, `test_pipeline_matrix`, `rag_integration_worker`: 임시 실제 DB·청크설정 변화·공시 격리 통과 | 구현됨. 실제 BGE 벡터 품질·운영 문서별 청크 수 미검증. 이번 재색인 없음 | 부분 충족 |
| F2006 / 선택 메타 고정·공시 범위 의미검색·Top-k/중복 제거 | `rag/retrieval.py:retrieve`, `services/candidate_search.py:search_candidates` | `test_candidate_search`, `test_pipeline_matrix`, `test_rag_pipeline`: 선택 메타/Top-k/다른 공시 제외·중복 제거 통과 | 구현됨. 실제 BGE 검색의 질문별 적합성/재현율은 미검증 | 부분 충족 |
| F2007 / LangGraph retrieve→generate·근거 한정 답변 | `rag/pipeline.py:build_graph,validate_answer`, `rag/prompts/answer.py`, `rag/claim_checks.py:validate_core_claims` | `test_rag_pipeline:test_actual_langgraph_nodes_and_empty_evidence_skip_generation`, `test_core_claims`: 실제 그래프/모의LLM·문서 밖 답변·핵심 주장 검증 통과 | 구현됨. 검증기는 모든 자연어 주장·금액·날짜 정확성을 보장하지 않는다. 실제 답변 전수 대조 필요 | 부분 충족 |
| F2008 / 인용 번호 검증·실제 인용 근거만 제목/URL/유형 표시 | `rag/pipeline.py:validate_answer`, `ui/chat.py:group_citations`, `app.py:render_result` | `test_rag_pipeline:test_only_actually_cited_evidence_displayed_in_first_use_order,test_invalid_or_missing_citations_and_extra_fields_rejected`, `test_analysis_presentation`: 통과 | 구현됨. 사용자 번호·원문 링크 정상 확인. 번호별 실제 title 표시 보완. 의미적 근거 일치는 F2007/F2010 별도 검증 | 충족 |
| F2009 / 공시별 대화 관리·전환 후 복원 | `ui/chat.py:history_for,save_turn,separate_legacy_analysis`, `app.py:histories,analyses,navigate`, `rag/conversation.py:recent_history` | `test_rag_ui`, `test_conversation`, `test_ui_shell`: 다중질문/공시 분리/전환복귀 통과; 사용자 실검증 | 구현됨. 추적표는 session_state 요구, F5 영구 복원 명시 없음. 사용자 선택으로 세션 저장 유지 | 충족 |
| F2010 / 사건·날짜·금액·위험·조건의 근거 있는 핵심 분석 | `rag/prompts/answer.py:ANALYSIS_QUESTION`, `app.py:run_question`, 기존 `Runtime.ask`/`validate_answer` 경로 | `test_core_claims`, `test_rag_ui`, `test_analysis_presentation`: 프롬프트/수치·권리 주장 거절 회귀·근거 표시 통과 | 구현됨. 사용자 생성 성공 확인. 실제 원문과 실제 핵심 분석 수치/위험 해석 직접 대조 없음. 정확성 완료 선언 불가 | 부분 충족 |
| F2011 / 자연어 조건 해석·후보 선별·결과에서 분석 이동 | `services/discovery.py:interpret_condition,discover,empty_message`, `app.py` 보관 공시 자연어 검색 | `test_rag_pipeline:test_vector_candidates_are_filtered_by_independent_condition_prompt,test_empty_results_skip_llm_and_empty_condition_rejected`, `test_rag_ui`: 모의 통과; 사용자 자연어/빈 결과 확인 | 구현됨. 메타데이터만으로 본문 조건 충족을 확정하지 않는다. 실제 복합 조건의 선별 품질은 미검증 | 부분 충족 |
| F2012 / 실제 진행·오류·빈 결과·면책 안내 | `app.py:run_question`, `ui/search.py:render_search`, `ui/recent.py`, `config.py:DISCLAIMER` | `test_analysis_presentation` 진행/실패/미실행단계 미표시, `test_ui_shell`, `test_search_ui`, `test_recent_ui`, 키/실패 테스트 통과 | 구현됨. 실제 콜백만 정제해 표시, 성공은 ask 반환 후, 실패는 error. 실제 API 처리 중 브라우저 타이밍 미검증 | 충족 |

## 발견 문제와 최소 수정

1. **P0 진행 상태:** 기존 콜백은 서버 로그만 갱신했다. 같은 콜백을 status에 연결했다.
   알려진 단계만 사용자 표현으로 매핑하고 원문/예외 메시지는 표시하지 않는다.
   원문 수집·읽기·색인 확인/준비·근거 검색·답변 작성은 해당 이벤트가 있을 때만 표시한다.
   캐시 경로의 색인 검증 이벤트를 새 임베딩 생성 완료라고 표현하지 않는다.
   `완료` 이벤트만으로 성공 처리하지 않고 Runtime.ask 반환 뒤 완료, 예외 시 실패 처리한다.
2. **F5 초기화:** histories/analyses/record_labels는 세션 메모리이며 SQLite 수집 원장에 없다.
   새 연결은 새 세션이므로 URL은 공시를 복원해도 대화를 복원하지 않는다.
   원장을 대화 테이블로 오용하면 사용자 간 혼입·공유PC 노출·다중세션 충돌 위험이 있다.
   영구 저장은 사용자/대화 식별·스키마·수명·삭제·인증·동시성·마이그레이션/회귀 테스트가 필요한 별도 작업이다.
   단순 UI 수정 비용 수준을 넘으며 무인증 공시번호만으로 공유 저장하는 방식은 권장하지 않는다.
   사용자에게 범위를 설명하고 **이번에는 세션 저장 유지** 답변을 받았다. 저장 스키마 수정 없음.
3. **분석 동선:** 보관 공시가 분석 대상이라는 안내를 추가했다. 전체 검색 결과에 분석 가능/미지원 표시,
   접수번호·기업코드가 기존 색인과 일치할 때만 AI 분석 화면 직행 버튼을 추가했다.
   버튼 자체는 Runtime.ask를 실행하지 않는다. 미색인 분석 기능은 추가하지 않았다.
4. **추가 질문:** 저장된 질문/답변을 먼저 출력하고 입력창을 마지막에 배치했다. 기존 history 전달·저장 유지.
5. **근거:** Evidence에 title/source/id가 실제로 존재하고 기존에는 그룹 제목만 위에 표시했다.
   이제 각 `[근거 N]` 옆에 그 citation의 title을 표시한다. 없는 제목은 '문서명 미제공', 임의 생성 없음.
   source별 그룹/번호/URL/본문·청크 식별 상세/경고는 보존한다.

## 실행 결과

- 변경 전 전체 **263 통과**, 41.458초.
- 변경 후 전체 **267 통과/실패0/skip0**, 44.961초.
- 신규 4개: 실제 진행/미실행단계 및 오류, 대화 뒤 입력 순서, 번호별 실제 제목,
  식별자가 일치하는 색인 공시 분석 직행 및 자동 RAG 실행 없음.
- 기존 기술명 비노출 테스트는 새로운 F2012 실제 진행 표시 요구에 맞춰 status 사용을 검증하도록 갱신했다.
- 중간 신규 테스트 fixture의 collected_at 인자 누락1건을 수정한 뒤 전체 재실행했다. 운영 로직 실패는 없었다.
- pip check, git diff 검사 통과. .env, .streamlit/secrets.toml, .local 추적0 및 ignore 확인.
- 이번 변경에 대한 실제 브라우저 수동 테스트는 미수행. 실제 API/모델 없이 AppTest와 격리 DB 회귀 실행.

## 남은 검증과 실행

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/streamlit run app.py
```

실제 운영 데이터는 읽기 전용으로 원문과 답변을 문장 단위 대조해야 한다. 기업/문서/수치/단위/기준일·
위험 조건·인용 구절 일치를 기록하고, 누락/모호한 근거는 검증 완료로 판정하지 않는다.
기존 Chrome의 과거 쿼리 history 복원 한계는 `disclosure-search.md`대로 남으며 이번 수정 대상 밖이다.
이번 새 UI의 검증은 AppTest 자동 회귀이며 실제 API/사용자 브라우저 실검증으로 대체하지 않는다.
