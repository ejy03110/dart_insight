# 실행 검증 기록

## GMS 전체 호출 경로 통합 안정화 (2026-10-22 UTC)

- 검색어 추출/후보 선별/답변 생성의 HTTP 처리를 작은 공통 post_chat 함수로 통합.
  401/403 인증, 404 모델/경로, 429 한도, 5xx 서버, timeout/network, 400/기타 요청 오류를 구분한다.
  HTTP/통신/출력 실패를 합쳐 호출당 최대 3회, 일시 실패만 1/2초 backoff. 모든 응답 자원 해제.
  공급자 코드/파라미터는 고정 allowlist만 표시하고 원문·키·임의 에러 메시지는 출력하지 않는다.
- 검색어 QueryExtractionError만 원래 질문+접수번호로 fallback. 원장에 추출 성공으로 저장하지 않으며
  다음 실행에서 재시도하고 UI/audit에 사용 사실을 유지한다. 후보 선별/최종 스키마·근거 검증은 우회하지 않는다.
  동일 인증/모델/요청 설정 오류는 후속 GMS 호출 없이 중단한다. 새 extract는 차단 상태를 초기화한다.
- 전체 **165개 통과 / 실패 0 / skip 0**, 13.879초(기존 157 + 신규 8). pip check 정상.
  Mock 상태별 정상/복구/소진/혼합 총예산/비노출, 실제 임시 Chroma/SQLite/LangGraph fallback·허위 인용 거절·
  정상 추출 복구·본문 재사용, AppTest 경고 유지 검증. 기존 HTTP 즉시 실패 테스트는 새 재시도 정책으로 갱신.
- 실제 Windows의 최근 추출 오류 HTTP 상태/응답은 제공되지 않아 원인 미확정.
  실제 GMS/OpenDART/BGE·Windows 브라우저는 미검증. .env/Chroma/SQLite/사용자 데이터 변경·삭제 없음.
- 최신 정책·Windows 재검증·남은 인증/모델/자원 장애 요인: [gms-recovery.md](gms-recovery.md).


## 실제 GMS 검증 로그 후속 진단 (2026-10-22 UTC)

- 사용자 실제 HTTP 200/finish=stop/schema_or_grounding 로그는 JSON 해석 후 콜백 검증 실패를 뜻한다.
  기존 메시지로는 필드/인용/내부 오류를 구별할 수 없다. 실제 응답/키가 없어 단일 원인 확정 불가.
- AnswerValidationError/SelectionValidationError로 schema/citation/grounding 및 정적 field/rule 구분.
  클래스/규칙/근거 개수만 기록하며 값·원문·임의 추가 필드명·예외 메시지는 출력하지 않는다.
  미분류 내부 검증 오류는 별도 코드로 즉시 실패, 교정 가능한 출력만 기존 한도 내 재시도.
- JSON 두 필드와 answer 내부 Markdown/boolean 자료형, 인용 number와 실제 청크 id 차이를 프롬프트에 명시.
  기존 검증 조건을 완화하거나 인용을 임의 보정하지 않는다. 분석/채팅 동일 경로의 정확한 ID 매핑 테스트 추가.
- 전체 **157개 통과 / 실패 0 / skip 0**, 13.146초(기존 150 + 신규 7). Mock 경계와 실제 LangGraph/임시 DB 회귀 검증.
  [진단 절차](gms-validation-diagnostics.md) 참고.
  실제 API/Windows 브라우저 미검증. 데이터/모델/DB 변경 없음.


## Q-11 성능 계측 / Q-12 GMS 검증 / 무료 배포 준비 (2026-10-22 UTC)

- GMS 실패 실제 응답/키는 없어 Windows 원인은 미확정. envelope/종료 사유/JSON/스키마·근거 오류 분리,
  전체 코드블록/문자열 text 블록 허용, 잘린 정상 JSON·거절·중복 키·NaN·잘못된 인용 거절.
  교정 가능한 출력 오류만 최대 2회 교정 재시도, HTTP/모델 구조 등은 구분해 안내한다.
  검색어 경로도 동일 envelope 진단/자원 해제 적용. 예전 envelope 무조건 재시도 테스트는 새 정책에 맞춰 즉시 실패로 갱신.
- 내용/키를 기록하지 않는 단계별 elapsed/HTTP/finish/token/문서·청크 재사용 계측 추가.
  다운로드/XML/청킹/모델 로드/임베딩/저장/검색/GMS 단계를 구분한다.
- 동일 요청 내 공시 중복 청킹·색인 검증 제거. 다음 요청에서는 DB 일관성 재검증 유지.
  성공한 본문/청크 임베딩 재사용 및 실패 재개 유지. 모델 최초 로딩 잠금, CPU batch 4 설정 추가(문서 절단 없음).
- 합성 417청크 실제 Chroma/SQLite/LangGraph 단일 비교: 최초 13.9662→12.5370초,
  재분석 0.1488→0.0825초. 재분석 다운로드/문서 임베딩 0, 질문 임베딩 1.
  BGE/GMS/HTTP는 테스트 구현이므로 실제 사업보고서 성능이나 품질 검증이 아니다.
- 전체 자동화 **150개 통과 / 실패 0 / skip 0**(기존 144 + 신규 6), pip check 정상.
  Linux Python 3.12.14 검증. 실제 OpenDART/GMS/BGE·Windows Python 3.11·Cloud 배포 미검증.
- BGE 공식 모델 API 가중치 파일 2,271,145,830바이트 확인. Cloud 현행 무료 자원 공식 문서는 접근 실패.
  무료 운영 가능을 보장하지 않으며 초기 DB 안전 전달/재시작 복구·메모리 실측이 배포 장애 요인.
  배포 실행/아키텍처 교체 없음. .env/SQLite/Chroma 사용자 데이터 삭제·덮어쓰기 없음.
- 설정·성능 재현·실제 Windows 검증·초기 데이터 설계: [cloud-readiness.md](cloud-readiness.md).


## Q-07 세 유형 통합 전처리 (2026-10-22 UTC)

- 사용자 전체 ZIP 진단 보고의 제목형 꺾쇠(공백 유무/인라인 포함)·단독 앰퍼샌드를 토큰 단위로 처리.
  정상 태그/Unicode 태그/속성/정상 엔티티/CDATA/주석/처리 명령은 보존. 일반 recover 파서로 본문을 버리지 않는다.
- 모든 XML의 엄격 재파싱을 유지하며 한 건이라도 실패하면 전체 본문 반환·RAG 색인을 거절한다.
  ZIP 100 MiB/1,000개·실제 읽기 크기·엔티티 선언 금지·경로 비추출 유지. 기존 데이터 삭제/스키마 변경 없음.
- 제목·앰퍼샌드·HTML 엔티티 보정 건수를 원장과 UI에 전달. 사용자 제공 실제 발췌를 3개 XML fixture에 배치해
  전체 추출 문자열을 비교했고, 임시 실제 Chroma/SQLite/LangGraph에서 새 유형까지 색인·근거·안내 유지 검증.
- 전체 자동화 **144개 통과 / 실패 0 / skip 0**, 13.678초. 기존 142개에 신규 2개 추가.
  외부 HTTP/LLM/임베딩은 Mock이며, 실제 OpenDART/BGE/GMS·Windows 브라우저 검증이 아니다.
- 실제 전체 ZIP은 클라우드에 없어 세 실제 XML의 엄격 재파싱/본문 무손실은 미검증.
  사용자 보고 100/52/52는 복구 파서의 진단 수이며 실제 보정 건수로 간주하지 않는다.
  [Windows 전체 ZIP 재검증](q07-q10-followup.md)의 단일 CLI로 모든 XML 검사 후에만 새 UTF-8 본문을 저장한다.


## Q-07 실제 제목형 XML / Q-10 복사 후속 수정 (2026-10-22 KST)

- 사용자 제공 실제 ZIP 발췌로 원인 확인: `20250311001085`, UTF-8 첫 XML 6,298,681바이트/총 3개,
  6263행 `<P>< TV 시장점유율 추이 ></P>`가 XML 태그 문법을 위반한다. 회귀 입력에서 행 6263/열 4 재현.
- 정상 P/SPAN의 단독 한글 다단어 제목에 한해 꺾쇠를 문자로 보존하는 제한적 보정.
  정상 태그/CDATA/주변 수치 보존, 모든 XML 검사, 추가 손상 시 부분 본문 반환/색인 거절.
  100 MiB·1,000개·엔티티/원격 DTD/ZIP 경로 제한 유지.
- 보정 내역은 선택 필드 parse_notes로 원장/재질문/분석·채팅에 유지. 빈 필드는 기존 JSON 형태를 유지하며
  SQLite 스키마·기존 완료 행을 바꾸지 않는다. inspect_document에 --show-repairs/--text-output 추가(새 UTF-8 파일만).
- Q-10: 공식 client.toolbarMode=viewer 설정으로 개발자 캐시 삭제 메뉴/단축키 경로 차단.
  localhost auto의 일반 c 팝업은 실제 Chromium 재현. Linux 정상 Ctrl+C 오작동 자체는 재현되지 않아 Windows와 구분.
  분석/채팅에 st.code 기본 복사 아이콘 추가. 임의 JavaScript 키 이벤트 가로채기 없음.
- **전체 142개 통과 / 실패 0 / skip 0**, 15.443초 (기존 135 + 신규 7). 실제 임시 Chroma/SQLite/LangGraph에서
  복구 텍스트→색인→근거→답변·안내 유지 검증. HTTP/LLM/BGE는 Mock.
- Linux Chromium 154 실제 클립보드: 선택/미선택 Ctrl+C, 두 복사 아이콘의 한글/줄바꿈 내용 일치,
  일반 c 팝업 없음·resource cache 유지 확인. Playwright는 /tmp 별도 환경에만 설치, 운영 의존성 변경 없음.
- **전체 실제 ZIP·나머지 XML 내용·실제 OpenDART/BGE/GMS·Windows 브라우저는 미검증**.
  클라우드에는 해당 ZIP·API 키·1,065건 DB가 없다. 사용자 제공 발췌와 테스트 fixture를 전체 실제 파일 검증으로 간주하지 않는다.
- 재검증 명령/근거: [q07-q10-followup.md](q07-q10-followup.md). 기존 .env/Chroma/SQLite/모델 데이터 삭제 없음.

## Windows E2E 오류 수정 검증 (2026-10-22 KST)

- **135개 통과, 실패 0, skip 0** / 12.214초 / Linux Python 3.12.14.
  기존 114개 + 신규 21개. 테스트 비활성화/실제 코드를 Mock 대체하지 않았다.
- 합성 ZIP: CP949 확장 한글·UTF-16 BOM·외부 DTD 비조회·알려진 엔티티/일반 앰퍼샌드·CDATA 보존·
  다중 XML·50,000행/2 MiB 초과 표·파일 수/압축 해제 크기 제한·미지정 엔티티/손상 XML 오류·경로 비추출.
  실패 원본 ZIP 보존 및 본문 비출력 진단 CLI를 임시 폴더에서 검증했다.
- AppTest: 분석/채팅 상태 분리·후속 질문 시 분석 미재실행·공시별 복귀·단일 URL/여러 인용 청크 추적·
  날짜 변경 즉시 명령 반영 및 확인 요청과 일치·미래 연도 없음/제출연도 설명/본문 수치 안내 구분.
- 검색/답변: 2025 제출과 2024 사업연도, 벡터 Top-k 밖의 실제 인덱스 후보 보완, 2035 없음,
  장내매수와 목적 혼동 거절/목적 미확인 안내, 기존 무근거/잘못된 인용 거절 테스트.
- Q-04: 외부 검색에서 기간별 보고서 2개 후보 생성, 실제 임시 Chroma에서 단일/추가 선택 문서 색인과 공시 격리 검증.
  같은 검색어를 사용하는 새 비교 질문은 후보만 재선별하고 이미 받은 원문/외부 검색을 반복하지 않는지 검증했다.
  임베딩/LLM/HTTP는 Mock이므로 실제 검색/선별 품질의 증거가 아니다.
- pip check·compileall·git diff --check 통과. 기존 .env·실제 SQLite·메타 DB를 삭제/초기화하지 않았다.
- **Q-07 실제 원인 미확정**: 이 머신에 실패 사업보고서 ZIP과 DART/GMS 키, 실제 1,065건 DB가 없다.
  OpenDART 루트 HEAD는 HTTP 503 관측. 인증된 document.xml/GMS·실제 BGE 추론은 미실행이다.
  합성 입력의 통과를 실제 사업보고서 해결 완료로 표시하지 않는다.
- 사용자 Windows E2E 성공/오류 보고와 위 자동화 검증을 구분한다. 재검증 순서는 [e2e-fixes.md](e2e-fixes.md).

## 필수 통합 검증 (2026-10-22 KST)

사용자 첨부 공식 PDF 23쪽과 통합 개발 프롬프트를 확인했다. 필수 F2001~F2012만 대상으로 하며
[공식 요구사항 대조표](required-integration.md)를 별도 작성했다. 아래 이전 기록은 당시 상태다.

| 검사 | 결과·범위 |
|---|---|
| 전체 unittest | **114개 통과 / 실패 0 / skip 0**, 13.125초. Linux Python 3.12.14 |
| 신규 RAG/UI | 23개 통과. ZIP/XML·euc-kr·엔티티 거절·Bundle·GMS 오류/JSON/인용 번호·실제 그래프·조건 선별·UI 전환 |
| Persistent 통합 worker | 실제 임시 Chroma·SQLite·LangGraph, Mock 1024벡터/LLM/수집. 재수집 없음·부분 다운로드/임베딩 실패 재개·항목 격리·재청킹·문서 DB 복구 |
| Streamlit AppTest | 개별 공시 질문/전환/복귀·위험 분석·조건 검색에서 공시 선택·무인용·안전한 오류, 예외 없음 |
| 의존성/구문 | pip check / compileall 통과. 새 의존성 추가·버전 갱신 없음 |
| CLI | ask/export_documents --help 정상. 실제 API 명령은 키/자료 부재로 미실행 |
| Git | 기존 사용자 변경 없음. 기존 F2004 및 Windows 수정 계보 보존 merge. git diff --check 통과 |
| 실제 환경 앱 시작 | 실제 설정/키 없음/DB 없음에서 AppTest 예외 0, 키·색인 안내 정상, 서버 health ok |
| 실제 API/모델 | **미실행**. DART_API_KEY/GMS_API_KEY/OPENAI_API_KEY 설정 부재, 실제 1,065건 자료·모델 캐시 없음. OpenDART·Hugging Face CONNECT 403 |
| Windows 11/Python 3.11 | **직접 미실행**. requirements.txt 설치 절차 안내, Chroma 통합 검증은 자식 프로세스 격리 |
| 실제 서비스/제출 증거 | 실제 응답 충실성·조건 선별 품질·NF2001 시간·스크린샷·셀프/상호 검증 미확인 |

Mock 파이프라인의 통과는 실제 LLM의 사실 정확성·근거 충실성이나 실제 BGE 검색 품질을 입증하지 않는다.
허용 ID·인용 번호 검증은 존재하지 않는 참조를 막는 구조 검증이며 주장과 문서의 의미 일치까지 보장하지 않는다.
실제 키·도메인·자료를 준비한 뒤 README E2E 명령과 공식 대조표의 미체크 항목을 실행해야 한다.


## F2002 로컬 BGE-M3 전환 검증 (2026-10-21 KST)

- 사용자 GMS embeddings 403 보고에 따라 실제 임베딩 경로를 sentence-transformers BAAI/bge-m3로 변경했다.
  문서·검색어 dense 1024차원·정규화·동일 팩토리, 키 불필요. GMS LLM 연결은 후속 단계다.
- 기존 --input/--db-dir/--batch-size/--dry-run 유지. F2001 코드·JSON 데이터는 변경하지 않았다.
- CPU 의존성 설치·pip check·오프라인 SentenceTransformer import 통과. 모델을 생성하거나 다운로드하지 않았다.
- 전체 **58개 테스트 통과, 실패/skip 0**. HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1에서 실행했다.
  기존 48개를 로컬 방식에 맞게 갱신하고 10개를 추가했다. 실제 외부 API·모델 다운로드는 없었다.
- 지연 로딩·문서/질의 동일 인코더·정규화·1024차원·기업 필터 검색·재실행 생략·기존 OpenAI 벡터 보존·
  모델/프로필/차원 불일치 차단·검색 CLI를 Mock으로 검증했다.
- 실제 1,065건 파일은 Codex에 없으며 실제 BGE-M3 추론·소요 자원·검색 품질은 **미검증**이다.
  기존 DB는 삭제하지 않고 새 경로에 같은 JSON을 재색인하도록 안내한다. 상세 `f2002.md` 참조.

아래 OpenAI 기반 F2002 기록은 전환 이전의 역사적 결과이며 현재 실행 방식은 위 기록을 따른다.

## F2002 후속 구현 검증 (2026-10-21 KST)

- 관련 기준: 원본 PDF p.11~12의 F2002. F2001 수집 코드는 변경하지 않았다.
- F2001 실제 결과는 사용자 로컬에서 1,065건(삼성전자 827 / 현대자동차 172 / 금양 66),
  `summary.status=complete`로 보고받았다. 해당 파일은 Codex에 없어 직접 재검증하지 않았다.
- 신규: `vectorstore/metadata.py`, `index_metadata.py`, `tests/test_metadata_indexing.py`, `docs/f2002.md`.
  `vectorstore/__init__.py` 설명과 README·분석·설계·검증 문서를 갱신했다.
- F2002 테스트 **13개 통과**, 전체 회귀 테스트 **48개 통과**(실패/skip 0).
  실제 임시 Persistent Chroma와 Mock 임베딩을 사용하여 100건 색인·같은 명령 재실행 시 수 불변,
  재실행 임베딩 0건, corp_code 필터(34/33/33), 텍스트 변경 시 같은 ID 갱신,
  모델 불일치 차단, 잘못된 벡터 거절, 부분 실패 후 재개, 기존 레코드 보존을 확인했다.
- 입력 검사는 필수 필드·ID·중복·날짜·키 없는 URL·색인 텍스트·전체/기업별 건수 불일치를 확인한다.
- CLI dry-run을 `/tmp`의 합성 F2001 100건 파일로 별도 실행해 문서 수·모델·기업별 집계 출력을 확인했다.
  테스트에서도 dry-run의 임베딩 팩토리/DB 미호출, 키 없는 실행 차단, 호출 전 문서 수·모델 출력을 검증했다.
  CLI `--help`, `git diff --check` 통과.
- **실제 OpenAI API 호출은 수행하지 않았다.** 사용자 1,065건의 실제 임베딩·검색 품질·실제 비용·
  실제 API로 두 번 실행한 결과는 미검증이다. 실행/비용/재색인 증빙 절차는 `f2002.md` 참조.

아래 F2001/초기 기록은 각 작업 당시의 결과다. 현재 F2002·전체 테스트 수는 위 기록을 우선한다.

## F2001 후속 구현 검증 (2026-10-21 KST)

- 추가 파일: `dart_insight/data/collection.py`, `dart_insight/collect.py`, `tests/test_collection.py`, `docs/f2001.md`.
- 수정 문서: README, 요구사항 분석, 구조 설계, 이 검증 문서. 관련 구현 요구사항은 **F2001만**이다.
- 기존 DartClient/Company/Disclosure/JsonCache와 API 호출·캐시 정책은 그대로 재사용한다.
- 전체 테스트 **35개 통과**: 기존 18개 + 신규 17개. 실패/skip 0.
  신규 검사는 3기업 공식 코드 매칭 호출, 모든 기업·유형·페이지 처리,
  유형 간 중복/분류 보존, 100건 기준, 과거 범위 확대·윤년·하한, 기업별 자료 존재,
  빈/부족 결과, 인증/한도/통신 실패 중단, 키 누락, JSON 저장·재로드·이전 실행 보존을 포함한다.
- 실제 DartClient와 JsonCache를 사용하고 HTTP만 대체한 테스트에서 기업코드 ZIP과
  2페이지 이상 목록, 두 번째 수집의 캐시 재사용, 저장 파일의 키 비포함을 확인했다.
- Codex의 `load_settings()` 확인 결과 DART_API_KEY 없음. 실제 API 요청·실제 100건 확보는 **미검증**.
  사용자가 별도 환경에서 확인한 삼성전자 정기공시 4건은 이번 Codex 검증 결과로 계산하지 않는다.
- 수집 CLI `--help` 정상 출력. 키 없는 실제 CLI 실행은 안내 메시지와 종료 코드 1을 반환했고
  성공 결과를 저장하지 않았다. 새 모듈 구문 컴파일과 `git diff --check`도 통과했다.
- 실제 검증 명령과 저장 증빙 항목은 `f2001.md` 참조. F2002·원문·RAG는 여전히 미구현이다.

아래는 초기 구조 작업 당시의 기록이며, 테스트 수와 구현 범위는 위 후속 기록을 우선한다.

검증일: 2026-10-21 (KST). 작업 경로: `/workspace/dart_insight`.
Python 3.12.14 / Linux. **이번 검증은 초기 구조·연결 로직이며 전체 서비스 검증이 아니다.**

## 실행 결과

| 검사 | 결과 | 증거·범위 |
|---|---|---|
| PDF 전체 검토 | 완료 | 23쪽 텍스트 검토. 기능/비기능/검증/산출물 계획 반영 |
| PDF 필수 패키지 버전 존재 | 통과 | PyPI JSON으로 필수 14개 지정 버전 배포 확인 |
| `.venv/bin/python -m pip install -r requirements.txt` | 통과 | PDF 지정 버전 14개 + defusedxml 설치 |
| `.venv/bin/python -m pip check` | 통과 | `No broken requirements found.` |
| 주요 RAG 의존성 import | 통과 | ChatOpenAI/OpenAIEmbeddings/Chroma/RecursiveCharacterTextSplitter/StateGraph/chromadb import. 실제 임베딩·생성 실행 아님 |
| `pip freeze` → `requirements.lock.txt`, `pip install --no-input -r requirements.lock.txt` | 통과 | Linux/Python 3.12 현재 환경에서 재설치 확인. 별도 새 머신 복원 검증은 하지 않음 |
| `python -m unittest discover -s tests -v` | 통과 | **18개 실행, 18개 통과, 실패/skip 0** |
| CLI `--help` | 통과 | 기업·기간·유형·페이지 제한·refresh 옵션 확인 |
| 키 없는 CLI `--refresh` | 기대 오류 확인 | 종료 코드 1, `DART_API_KEY` 설정 안내. 성공으로 처리하지 않음 |
| Streamlit 실제 서버 시작 | 통과 | headless 실행, health·루트 각각 HTTP 200 및 예상 응답 확인 |
| 초기 화면 동작 | 통과 | AppTest: 키 누락·면책, 빈 결과 안내, 연결 오류 안내. API는 모의 응답으로 격리 |
| `python -m compileall -q dart_insight app.py tests` | 통과 | 구문 컴파일 |
| `git diff --check` | 통과 | 공백 오류 없음 |
| 실제 OpenDART 인증·기업코드·공시 수집 | **미확인/환경 차단** | 키 이름 존재 여부 확인 결과 DART_API_KEY/OPENDART_API_KEY 모두 없음. 공식 가이드 HTTPS 요청은 프록시 터널 403 |
| 실제 100건 확보·ChromaDB 색인 | **미구현·미실행** | 후속 단계. 현재 수집 건수·색인 수를 성공으로 표시하지 않음 |
| OpenAI API·원문·청킹·검색·LangGraph·채팅·요약·조건 탐색 | **미구현·미실행** | 이번 요청 범위 밖. 모든 F2001~F2012 최종 요구사항 유지 |
| Community Cloud 배포 | **미실행** | 로컬 시작과 배포 성공을 구별 |

테스트 세부: 기업코드 ZIP·종목코드 매칭·누락 기업, API 인증 오류 XML,
잘못된 ZIP/엔티티, 페이지 순회·접수번호 중복 제거·유형과 URL 정규화,
캐시 적중·강제 갱신·만료·손상·키 비저장, 조회 없음,
인증/한도/서버 오류 미캐시, 네트워크/timeout 안전 오류,
잘못된 JSON/필드/식별자/기업 응답 거절, 잘못된 기간/페이지 입력 차단,
설정 우선순위·비밀 repr 비노출·잘못된 설정, 초기 UI 상태.
테스트의 가상 응답은 `tests/`에서만 사용하며 실제 데이터로 저장/성공 처리하지 않는다.

Streamlit AppTest 실행 시 나타난 `missing ScriptRunContext` 경고는 테스트 실행 문맥 경고이며,
화면 예외는 0건이었다. 테스트는 브라우저 시각 검수나 제출용 캡처를 대신하지 않는다.
검증에 사용한 서버 프로세스는 종료했으며 README 명령으로 재시작할 수 있다.

## 실제 연결 검증을 위한 남은 조치

환경 설정 초안에는 `install_script`(잠금 파일 설치·pip check),
`start_skill`(기존 체크아웃 사용·테스트·연결 확인·Streamlit 재시작),
네트워크 사용자 도메인 `opendart.fss.or.kr`/`dart.fss.or.kr`,
`DART_API_KEY` 비밀값 요구사항이 저장되었다. 실제 키 값은 저장하지 않았다.
도구는 `status=saved`, `requires_publish=true`를 반환했다.
초안 저장만으로 런타임 변경·게시·새 인스턴스 복원이 검증된 것은 아니다.

1. 환경 설정에서 `DART_API_KEY` 값을 안전하게 입력한다. 채팅으로 공유하지 않는다.
2. 저장된 초안의 `opendart.fss.or.kr`, `dart.fss.or.kr` 추가를 검토·저장하고 환경을 게시한다.
   설정 저장은 런타임 적용/게시/인증 성공을 의미하지 않는다.
3. README의 세 기업별 CLI 명령을 `--refresh`로 실행하여 실제 기업코드·응답·출처를 확인한다.
4. 공식 이용 안내를 다시 확인하여 출처 문서에 이용 조건을 보완한다.
5. 이후 별도 단계에서 100건 확보·색인과 나머지 필수 기능을 구현하고 PDF 4장의 검증을 수행한다.

## 변경 파일·요구사항 대응

| 변경 | 파일 | 관련 요구사항 |
|---|---|---|
| 기존 파일 추가 설정 | `.gitignore` (`.local/`만 추가) | NF2003 |
| 신규 문서 | `README.md`, `docs/requirements-analysis.md`, `docs/architecture.md`, `docs/sources.md`, `docs/validation.md` | F2001~F2012 전체 계획·검증 추적, NF2007 |
| 신규 설정 | `.python-version`, `.env.example`, `.streamlit/secrets.toml.example`, `requirements.txt`, `requirements.lock.txt` | F2012 기반, NF2003/NF2005 |
| 신규 실행 진입점 | `app.py`, `dart_insight/cli.py` | F2001 연결 확인·F2012 초기 상태/오류 |
| 신규 설정 모듈 | `dart_insight/config.py` | F2005/F2006의 후속 설정 기반, F2012, NF2003/NF2004 |
| 신규 데이터/수집 | `dart_insight/data/models.py`, `dart_insight/data/cache.py`, `dart_insight/opendart/client.py` | F2001 기반, F2004 수집의 선행 연결, NF2002/NF2006 |
| 신규 패키지 경계 | `dart_insight/__init__.py`, `data/__init__.py`, `opendart/__init__.py`, `vectorstore/__init__.py`, `rag/__init__.py`, `rag/prompts/__init__.py`, `services/__init__.py`, `ui/__init__.py` (모두 `dart_insight/` 하위) | NF2005. F2002~F2012 기능 완료를 의미하지 않음 |
| 신규 테스트 | `tests/test_opendart.py`, `tests/test_config_cache.py`, `tests/test_app.py` | 초기 F2001/F2012 기반·비기능 연결 로직 검사 |

`.venv/`, `__pycache__/`는 로컬 설치/생성물이며 Git 제외다.
새 저장소·별도 프로젝트·worktree·커밋·푸시·배포는 만들거나 수행하지 않았다.
