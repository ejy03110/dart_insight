# 초기 단계 검증 결과

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
