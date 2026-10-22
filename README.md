# DART Insight

삼성전자·현대자동차·금양의 **개별 공시**를 선택해 공시 원문에 근거한 질의응답과 위험 요인 분석을 제공하는 Streamlit 서비스입니다. 공식 과제 PDF p.11~15의 **F2001~F2012만 구현 범위**입니다. 심화 F2013~F2016은 구현하지 않습니다.

현재 코드에는 메타데이터 수집·BGE-M3 색인, GMS 검색어 추출·문서 후보 선별, OpenDART ZIP/XML 원문 수집, 별도 문서 컬렉션, LangGraph 검색/생성, 인용 출처, 공시별 채팅, 핵심 내용·위험 요인 분석, 자연어 조건 탐색이 연결되어 있습니다. **실제 API/모델의 통합 실행 완료를 뜻하지 않습니다.** 이번 클라우드 검증은 Mock 외부 호출·임베딩 + 실제 Chroma/SQLite/LangGraph/UI 테스트입니다. 사용자 로컬 1,065건 수집·색인/GMS 성공 보고와 클라우드 직접 검증을 구분합니다.

이 서비스는 공시 근거 기반 정보 제공 목적이며 투자 조언이 아닙니다. 답변은 금융·투자 전문가의 조언을 대신하지 않습니다.

## Windows 11 / Python 3.11 실행

기존 저장소 루트에서 PowerShell을 실행합니다. 기존 가상환경과 데이터를 보존하세요.

```powershell
# 새 환경인 경우만 생성/설치. 기존 정상 .venv는 그대로 사용할 수 있습니다.
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
# .env가 없을 때만 예제를 복사합니다.
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

`requirements.lock.txt`는 **Linux/Python 3.12용 검증 잠금 파일**입니다. Windows에는 `uvloop` 등 플랫폼 비호환 항목이 있으므로 위의 직접 의존성 버전 고정 `requirements.txt`를 사용합니다. 이번 실행 머신은 Linux/Python 3.12이며 Windows/Python 3.11 직접 실행은 미검증입니다. 버전을 임의로 올리거나 기존 벡터 DB를 재생성할 필요는 없습니다.

무시된 `.env`를 로컬 편집기로 열고 다음을 설정합니다. 키 값은 채팅·Git·로그로 공유하지 마세요.

- `DART_API_KEY`: OpenDART 인증키. 원문/메타데이터 수집에 사용.
- `GMS_API_KEY`: SSAFY GMS 키. 기존 `OPENAI_API_KEY`도 지원하며 둘 다 있으면 `GMS_API_KEY` 우선. 직접 OpenAI 엔드포인트에는 전송하지 않습니다.
- `OPENAI_CHAT_MODEL=gpt-4.1-mini`
- `CHROMA_DB_DIR=.local/data/chroma_bge_m3`: **기존 1,065건 BGE DB** 경로. 기존 컬렉션·1024차원·정규화 프로필을 확인하며 다른 모델 DB는 덮어쓰지 않습니다.
- `EMBEDDING_MODEL=BAAI/bge-m3`, `EMBEDDING_DEVICE=cpu`

환경변수 > Streamlit Secrets > `.env` > 기본값 순서이며 GMS 별칭 간에는 `GMS_API_KEY`가 우선합니다. CLI는 Streamlit Secrets 파일을 직접 읽지 않습니다. 앱 시작·import·테스트만으로 LLM 호출이나 모델 다운로드가 일어나지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

1. 사이드바의 메타데이터 색인 건수와 기업을 확인하고 개별 공시를 선택합니다.
2. 질문을 입력하면 검색어 생성 → 외부 출처/내부 후보 검색 → LLM 선별 → 원문 수집 → 청킹·색인 → LangGraph `retrieve → generate` → 인용 출처가 이어집니다.
3. '공시 핵심 분석 실행' 버튼은 같은 근거 파이프라인을 사용하되 결과를 별도 영역에 저장합니다. 후속 질문은 '공시 관련 추가 질문' 채팅에만 저장하며 분석을 다시 실행하지 않습니다.
4. 자연어 조건 검색은 메타데이터 후보 중 LLM이 조건을 확인한 공시만 제시합니다. 결과 버튼을 누르면 그 공시 채팅으로 이동합니다. 본문 수치처럼 메타데이터에 없는 조건은 추측해 통과시키지 않도록 프롬프트로 제한합니다. 실제 LLM의 조건 준수·답변 품질은 실검증이 필요합니다.
5. 대화는 브라우저 세션 안에서 `rcept_no`별로 유지합니다. 서버 재시작 이후의 대화 영구 저장 기능은 포함하지 않습니다.

## 데이터가 없는 새 환경

실제 키가 설정되고 외부 호출이 허용된 환경에서 실행합니다. 기존 데이터가 있다면 수집·전체 재색인을 반복할 필요가 없습니다.

```powershell
.\.venv\Scripts\python.exe -m dart_insight.collect --begin 2025-01-01 --end 2025-12-31
# 실제 보유 파일 경로로 바꾸세요. 특정 과거 run-* 폴더는 가정하지 않습니다.
$collectionFile = ".local/data/metadata/실제-run-폴더/collection.json"
.\.venv\Scripts\python.exe -m dart_insight.index_metadata --input $collectionFile --dry-run
.\.venv\Scripts\python.exe -m dart_insight.index_metadata --input $collectionFile --db-dir .local/data/chroma_bge_m3 --batch-size 16
# 같은 명령을 다시 실행해 count_delta=0, embedded_count=0 확인
.\.venv\Scripts\python.exe -m dart_insight.index_metadata --input $collectionFile --db-dir .local/data/chroma_bge_m3 --batch-size 16
```

모델 최초 사용 시 Hugging Face 가중치 다운로드가 필요합니다. 완전히 캐시한 뒤에만 `HF_HUB_OFFLINE=1`을 사용하세요. 로컬 BGE 임베딩에는 LLM 키를 사용하지 않습니다.

UI 없는 실제 E2E 실행은 **DB에 실제 있는 접수번호**로 수행합니다. 이 명령은 실제 GMS 호출을 발생시킵니다.

```powershell
$receipt = Read-Host "색인된 14자리 공시 접수번호"
.\.venv\Scripts\python.exe -m dart_insight.ask --receipt $receipt --question "이 공시에 명시된 위험 요인은 무엇인가요?"
```

## 외부 문서 접근 제한 시 실제 문서 묶음

PDF F2004가 허용하는 수집 대체 방식입니다. 가짜 본문을 생성하지 않습니다. 접근 가능한 환경에서 실제 OpenDART 원문을 내보낼 수 있습니다.

```powershell
$receipt = Read-Host "색인된 실제 접수번호"
.\.venv\Scripts\python.exe -m dart_insight.export_documents --receipt $receipt --output .local/data/originals.json
```

생성한 실제 파일을 제한 환경에 옮기고 `.env`의 `DOCUMENT_BUNDLE=.local/data/originals.json`으로 지정합니다. 선택 공시 원문은 묶음에 반드시 있어야 합니다. 이 모드에서는 묶음의 실제 공시만 후보로 쓰며 원문 API를 호출하지 않습니다. **GMS 키와 BGE 모델/메타데이터 DB는 여전히 필요합니다.** 임의 JSON 생성은 실제 자료 검증을 대신하지 않습니다. 형식은 [통합 설계](docs/required-integration.md)에 있습니다. PDF·위키 등 서로 다른 출처를 결합하는 심화 F2016은 포함하지 않습니다.

## 청킹·검색·재실행

- `CHUNK_SIZE=1000`, `CHUNK_OVERLAP=200`: 환경 설정으로 조정. 변경 시 저장된 원문을 재사용해 해당 공시/문서만 재청킹합니다.
- `TOP_K=5`: 선택 메타데이터 1건 + 외부 문서 최대 4건. `TOP_K=1`이어도 선택 메타데이터는 유지합니다.
- `CANDIDATE_TOP_K=5`: 검색어별 후보 수(1~20). 검색어는 최대 6개 + 강제 접수번호.
- `DOCUMENT_SEARCH_DAYS=366`: 선택 공시 접수일 이전의 외부 후보 조회 범위. OpenDART는 자유 텍스트 검색 API가 없어 해당 기업의 A~J 전체 유형·전체 페이지를 조회하고 제목/접수번호 용어로 좁힙니다. 기존 벡터 후보도 보완합니다. 전 웹 검색이나 무제한 과거 검색 기능은 아닙니다.
- `.local/data/collection.sqlite3`: 정규화 검색어별 상태·선별 계획·개별 다운로드·질문별 검색어 저장. 완료 검색어는 재수집하지 않고, 실패한 단계는 저장 지점에서 재개합니다. 여러 수집 작업자의 동시 실행을 위한 분산 잠금은 제공하지 않으므로 한 저장소에 수집을 동시에 실행하지 마세요.
- 컬렉션 `dart_metadata`와 `dart_documents`는 분리합니다. 문서 청크 ID는 선택 공시·원문 공시·본문/설정 해시·순번을 포함합니다. 같은 원문이 여러 공시에 연결되어도 서로 덮어쓰지 않습니다.
- 완료 원장만 믿지 않고 실제 청크를 확인하므로 문서 DB가 비워지거나 청킹 설정이 바뀌어도 복구합니다. 기존 원문/다른 항목 DB를 일괄 삭제하지 않습니다.

## Linux 클라우드 개발·검증

```bash
cd /workspace/dart_insight
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-input -r requirements.lock.txt
.venv/bin/python -m pip check
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m streamlit run app.py --server.headless=true --server.port=8501
```

필요 도메인은 설치 시 PyPI/PyTorch, 실제 수집 시 `opendart.fss.or.kr`, 원문 링크 `dart.fss.or.kr`, LLM `gms.ssafy.io`, 최초 모델 다운로드 시 `huggingface.co` 및 배포 CDN입니다. 이번 클라우드는 키 미설정·일부 목적지 프록시 403으로 **실제 1,065건 E2E·모델 추론은 미검증**입니다. 환경 초안 저장과 게시·런타임 반영은 별개입니다.

- [Q-07 실제 제목형 XML 오류·Q-10 복사 후속 수정](docs/q07-q10-followup.md)
- [Windows E2E Q-01~Q-08 수정·사업보고서 ZIP 진단·재검증](docs/e2e-fixes.md)
- [공식 PDF 대조표·설계·실검증 체크리스트](docs/required-integration.md)
- [최신 진행 현황](docs/progress.md), [검증 기록](docs/validation.md)
- [F2001 수집](docs/f2001.md), [F2002 색인](docs/f2002.md), [기존 F2004 브랜치](docs/f2004.md)
- [출처와 이용 조건](docs/sources.md)

Q-11/Q-12 계측·GMS 오류 분류와 무료 배포 준비: [cloud-readiness.md](docs/cloud-readiness.md). 실제 무료 배포 가능 여부와 API/Windows 검증 범위는 이 문서를 확인하세요.

실제 진단 ZIP 및 기업·공시 전환 통합 검증: [integrated-stability.md](docs/integrated-stability.md).

Q-09 멀티턴·응답 형식·본문 채팅 입력창: [q09-conversation.md](docs/q09-conversation.md).

## 관리자 진단

일반 사용자에게 진단 메뉴는 표시하지 않습니다. 관리자 비밀번호 해시 설정과 로그인·로그아웃,
인증 범위와 배포 시 제한은 [관리자 진단 접근 안내](docs/admin-access.md)를 참고하세요.
실제 비밀번호·해시·API 키는 Git에 저장하지 마세요.

## 최근 7일 공시

홈 또는 공시 탐색의 **최근 공시 불러오기**로 기존 DART 키를 사용해 조회합니다.
성공 결과는 5분간 재사용하며, 미색인 공시는 원문만 확인할 수 있습니다.
[조회 범위·캐시·기존 분석 연결 안내](docs/recent-disclosures.md)를 참고하세요.

최근 공시 상세의 **공시 본문 불러오기**로 AI 분석 없이 원문을 열람할 수 있습니다.
[지원 형식·목록 복귀·캐시 안내](docs/original-viewer.md)를 참고하세요.

## 전체 기간 공시 검색

공시 탐색의 **전체 기간 검색**에서 기업 선택 또는 전체 기업의 기간·유형·제목 조건을 검색합니다.
API 페이지 단위 조회, 검색 제한, URL 복원 범위는 [검색 안내](docs/disclosure-search.md)를 참고하세요.
