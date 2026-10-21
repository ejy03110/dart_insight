# DART Insight 개발 지침

이 지침은 저장소 전체에 적용한다. 새 작업을 시작할 때 `docs/progress.md`를 먼저 읽고
현재 코드·실행 증거를 확인한다. 사용자의 최신 요청 범위와 제약을 우선한다.

## 목표와 작업 범위

- OpenDART의 국내 기업 공시를 수집하고 자연어 질문에 공시 근거로 답하는 Python RAG 서비스다.
- 대상: 삼성전자 `005930`, 현대자동차 `005380`, 금양 `001570`.
- **항목 1건은 기업이 아니라 개별 공시 1건**이다. 식별자는 접수번호 `rcept_no`다.
- 공식 기업코드에서 종목코드로 기업을 찾는다. 기업코드를 이름이나 기억으로 추측하지 않는다.
- 필수 F2001~F2012를 우선한다. 심화 F2013~F2016으로 필수 기능을 대체하지 않는다.
- 자동 수집 스케줄러·회원가입·주가 예측은 제외한다. Streamlit Community Cloud를 고려해 저장 구조를 단순하게 유지한다.
- 금융 정보는 공시 근거 기반 정보 제공에 한정한다. 투자 권유나 전문가 조언을 대신하지 않는다는 면책을 표시한다.
- 한 번에 전체 서비스를 구현하지 않는다. 요청받은 기능을 구현하고 변경 파일·요구사항 번호·검증 결과·미검증 사항을 보고한다.

## 읽을 문서와 판단 기준

- 기준 문서: 원본 `AI코어프로젝트_2회차_요구사항정의서.pdf` 23쪽 전체.
  기획 p.7~8, 필수 구현 p.11~15, 비기능 p.10, 검증 p.18~19, 제출물 p.21~23을 함께 고려한다.
  원본은 저장소에 포함되어 있지 않다. 다른 대화의 첨부 파일·`/tmp` 경로가 그대로 존재한다고 가정하지 않는다.
- 현재 상태: [docs/progress.md](docs/progress.md).
- 구현 계획: [docs/requirements-analysis.md](docs/requirements-analysis.md).
- 구조·데이터·RAG 설계: [docs/architecture.md](docs/architecture.md).
- 실행 절차: [README.md](README.md), [docs/f2001.md](docs/f2001.md), [docs/f2002.md](docs/f2002.md).
- 실행 기록: [docs/validation.md](docs/validation.md). 출처·이용 조건: [docs/sources.md](docs/sources.md).
- 원본의 필수 조건과 예시·프로젝트 선택을 구분한다. 보조 문서가 원본을 대체하지 않는다.
  과거 검증 기록의 ‘미구현/미확인’과 최신 결과가 충돌하면 기록 시점과 근거를 확인하고 최신 상태를 갱신한다.
  구현·Mock 검증·사용자 로컬 실검증·Codex 실검증을 분리해서 기록한다.

## 기존 아키텍처와 재사용

| 위치 | 역할 |
|---|---|
| `dart_insight/config.py` | `Settings`, `load_settings()`, 키 비노출 설정·경로·모델·검색 설정·면책 |
| `dart_insight/data/models.py` | `Company`, `COMPANIES`, `Disclosure`, `to_index_text()` |
| `dart_insight/data/cache.py` | `JsonCache`: 성공 응답 TTL 캐시·원자적 저장 |
| `dart_insight/opendart/client.py` | `DartClient`: 공식 기업코드·유형별 목록·페이지 처리·안전한 오류·재시도 |
| `dart_insight/cli.py` | 기존 단일 기업 연결 확인. **`--type` 생략 시 A(정기공시)**, 페이지 제한 있음 |
| `dart_insight/data/collection.py`, `collect.py` | F2001 세 기업·유형별 전체 페이지 수집, 기간 확대, 중복 제거·집계·저장 |
| `dart_insight/vectorstore/metadata.py`, `index_metadata.py` | F2002 JSON 검증·dry-run·임베딩 주입·persistent Chroma upsert·검증 |
| `app.py` | 초기 Streamlit 연결 화면. 완성된 공시 선택·채팅 화면이 아님 |
| `rag/prompts/`, `services/`, `ui/` | 후속 구현 경계. 패키지가 있다는 이유로 기능 구현으로 판단하지 않음 |

기존 모델·클라이언트·캐시·설정을 재사용한다. 검증된 F2001 코드를 불필요하게 개편하지 않는다.
기존 격리 체크아웃에서 작업한다. 사용자가 요청하지 않으면 별도 저장소·프로젝트·Git worktree를 만들지 않는다.

기본 데이터 경로는 `.local/data`(`DART_DATA_DIR`로 변경 가능)다.
수집은 `metadata/run-*/collection.json`, 캐시는 `cache/`, 벡터 DB는 `chroma/`에 둔다.
실행별 폴더명을 코드에 고정하지 말고 입력 경로를 인자로 받는다. 이전 수집 결과·DB를 임의로 삭제하지 않는다.
로컬 데이터·DB·프로세스가 다른 환경이나 새 대화에 존재한다고 가정하지 않는다.

## 기술 스택

Python 3.10+이며 현재 검증 버전은 3.12다. Streamlit, LangChain, LangGraph, ChromaDB,
OpenAI API, OpenDART API, requests를 사용한다. LangSmith 등 PDF 지정 필수 패키지는
`requirements.txt`에 고정되어 있고, 검증 환경 전이 의존성은 `requirements.lock.txt`에 있다.
버전을 임의로 최신화하지 않는다. 추가/변경 의존성이 필요하면 이유와 호환성을 검증한다.
현재 F2002는 sentence-transformers의 로컬 `BAAI/bge-m3`(dense 1024차원·정규화)와 Chroma `PersistentClient`를 사용한다.
색인과 검색은 `vectorstore/embeddings.py`의 같은 팩토리를 사용한다. GMS는 향후 LLM용이며 임베딩에 사용하지 않는다.

## F2001~F2012에서 유지할 조건

| 번호 | 필수 조건 |
|---|---|
| F2001 | 실제 공시 고유 100건 이상. 전체 페이지·접수번호 중복 제거. 기업명·공시명·접수번호·접수일·유형·원문 URL이 있는 공시당 검색 텍스트 |
| F2002 | 단일 `dart_metadata`, `meta:{rcept_no}` upsert. 기업은 `corp_code`로 필터. 저장 수·재색인 중복 없음 확인 |
| F2003 | 도메인 전문가 역할의 검색어 추출 프롬프트. 파싱 가능한 출력. 선택 공시 자체 문서는 항상 검색 대상 |
| F2004 | 외부 후보 검색 → 독립된 후보 선별 프롬프트 → 본문·출처 수집. 선택 공시 원문은 모든 제외 규칙보다 우선해 색인. 이미 수집한 검색어 재수집 방지 |
| F2005 | 원문은 별도 `dart_documents` 컬렉션. 청크 크기·중복은 설정. 제목·출처·관련 항목 저장. 항목 간 같은 문서가 덮어쓰이지 않는 ID |
| F2006 | 선택 공시 메타데이터는 항상 근거에 포함. 질문 유사도로 원문 검색. 두 컬렉션 결과 병합·중복 제거. Top-k 설정 |
| F2007 | LangGraph `retrieve → generate` 두 노드. 독립 답변 프롬프트·문서 근거만 사용·출처 유형 구분. 근거 부족 시 확인되지 않는다고 답변 |
| F2008 | 주요 주장에 근거 번호. 실제 인용한 근거만 제목·원문 URL과 표시. 인용 없음 안내 |
| F2009 | 기업·개별 공시 선택과 채팅. 대화 기록은 `rcept_no`별로 분리·유지 |
| F2010 | 공시 핵심 요약 기능. 검색된 문서만 사용하고 근거 제공 |
| F2011 | 자연어 조건으로 후보 검색·조건 부합 공시만 선별. 선택 후 해당 공시 Q&A로 연결 |
| F2012 | 문서 수집·답변 생성 진행 상태. 키 누락·색인 없음·결과 없음·오류 구분. 금융 면책 |

실패·빈 결과를 가상 데이터로 대체하지 않는다. TTL 응답 캐시는 F2004/NF2002의 검색어 완료 원장을 대신하지 않는다.
NF2001~NF2007의 응답 시간·수집 캐시·키 보호·내부 정보 비노출·책임 분리·API 정책·출처/이용 조건도 검증한다.
외부 호출 제한 시 원본이 허용하는 제공 문서 묶음은 실제 자료일 때만 사용할 수 있다.

## API 키·외부 호출 보안

- 키는 환경변수·무시된 `.env`·Streamlit Secrets로만 관리한다. 소스·문서·테스트·로그·URL·Git에 실제 키를 쓰지 않는다.
- 이름과 설정 여부만 확인한다. 환경 전체나 자격증명 파일을 출력하지 않는다. 키 값을 채팅으로 요청하지 않는다.
- 설정 우선순위: 환경변수 > 전달된 Streamlit Secrets > `.env` > 기본값.
  CLI는 Streamlit Secrets 파일을 직접 읽지 않는다.
- `DART_API_KEY`: 실제 수집용. 로컬 임베딩·검색·dry-run·Mock 테스트는 API 키 불필요.
  `OPENAI_API_KEY` 등 LLM 키는 향후 생성용이며 현재 임베딩에서는 읽어 전송하지 않는다.
- 실제 OpenAI 호출은 사용자가 명시적으로 요청한 실행에 한정한다. 현재까지의 ‘실제 API 호출 금지’를
  새 승인 없이 해제하지 않는다. import·앱 시작·테스트가 자동으로 과금 호출을 하지 않게 한다.
- 모델 로드 전에 문서 수·모델을 알린다. 임베딩 기본값은 `BAAI/bge-m3`, CPU 실행이다.
  `EMBEDDING_MODEL`, `EMBEDDING_DEVICE`, `EMBEDDING_CACHE_DIR`를 사용하며 과거 `OPENAI_EMBEDDING_MODEL`은 무시한다.
  첫 실제 실행에 Hugging Face 가중치 다운로드가 필요할 수 있다. dry-run·테스트는 다운로드하지 않는다.
- 기존 timeout·간격·제한 재시도·TLS 검증·캐시 정책을 유지한다. 인증/한도/통신 오류를 빈 성공으로 바꾸지 않는다.
- 오류에는 정제한 메시지만 제공한다. 키·전체 프롬프트·provider 원문 응답·traceback을 UI에 노출하지 않는다.
- 모델 불일치 시 기존 컬렉션을 덮어쓰지 않는다. 다른 DB 경로를 사용하며 기존 자료를 보존한다.
  컬렉션 이름 `dart_metadata`는 유지하고 BGE 모델·정규화 프로필·1024차원 검증을 색인과 검색에 적용한다.

## 테스트와 진행 기록

저장소 루트에서 실행한다. 가상환경이 없으면 `python -m venv .venv` 후
`.venv/bin/python -m pip install -r requirements.lock.txt`로 준비한다.

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p test_metadata_indexing.py -v
.venv/bin/python -m dart_insight.index_metadata --input /실제/collection.json --dry-run
git diff --check
```

관련 변경에 맞는 의미 있는 테스트를 실행한다. 실제 키 없이 Mock 응답·임베딩과 임시 디렉터리를 사용한다.
사용자 데이터/DB를 테스트로 덮어쓰지 않는다. 0개 실행·skip·설치·import만으로 기능 통과를 선언하지 않는다.
F2002는 첫 색인/재색인의 건수·변경 없는 문서 생략·기업 필터·부분 실패 재개를 유지한다.
문서만 변경한 작업에는 불필요한 전체 테스트를 추가 실행하지 말고 링크·파일 범위·공백을 점검한다.
기능 변경 후 `docs/progress.md`에 요구사항·파일/함수·실행 결과·증거 출처·미검증 사항을 갱신한다.
원본 4장의 셀프/상호 체크리스트·필요 화면 캡처와 6장 제출물은 별도 확보해야 한다.

## Git 작업 규칙

- 작업 시작/종료에 현재 브랜치·`git status`를 확인하고 기존 사용자 변경을 보존한다.
- `master`에 직접 push하거나 병합하지 않는다. 작업 브랜치를 사용하고 PR 병합은 사용자가 검토 후 수행한다.
- 사용자는 작업 완료 때마다 작업 브랜치에 commit/push하도록 상시 요청했다. 이후 별도 확인 없이 요청 범위의 변경을 검증·보안 점검한 뒤 commit/push한다. 사용자가 특정 작업의 push를 금지하면 그 지시를 우선한다.
- 요청된 push 전 원격·브랜치 차이를 확인한다. `reset --hard`, 강제 push, 무단 삭제·기록 재작성으로 해결하지 않는다.
- 명시적인 파일 목록으로 stage하고 staged diff·민감정보를 검사한다. `.env`, 실제 `secrets.toml`,
  `.local/`, `.venv/`, 원본 비공개 PDF를 커밋하지 않는다. 예제 키 값은 비워 둔다.
- 성공한 push는 원격 커밋과 대조하고 브랜치·해시를 보고한다. Git push 가능 여부와 GitHub API/PR 생성 권한은 별개다.
- 문서만 요청된 경우 기능 코드는 수정하지 않는다. 문서 변경도 위 상시 요청에 따라 작업 브랜치에 commit/push한다.
