# DART Insight

삼성전자·현대자동차·금양의 개별 공시를 분석하는 Python RAG 서비스 프로젝트입니다.
현재는 **초기 구조·OpenDART 연결·F2001 메타데이터 수집·F2002 로컬 BGE-M3/Chroma 색인 CLI를 구현**했습니다.
사용자 로컬 검증에서 F2001 1,065건(827/172/66) 수집 완료를 보고받았습니다.
F2002는 Mock 임베딩으로 검증했으며 실제 BGE-M3 모델의 1,065건 색인은 미검증입니다.
GMS 임베딩 403 문제에 따라 임베딩은 로컬 `BAAI/bge-m3`, 향후 LLM 호출은 SSAFY GMS로 분리합니다.
원문 수집·청킹, LangGraph, 채팅·요약·조건 탐색은 후속 단계입니다.
F2001~F2012는 모두 최종 필수이며 현재 전체 충족을 선언하지 않습니다.

- [F2001~F2012 구현·검증 계획, 패키지/환경변수, 3일 일정](docs/requirements-analysis.md)
- [폴더 구조·데이터·두 컬렉션·RAG 설계](docs/architecture.md)
- [출처·이용 조건](docs/sources.md)
- [실행 검증 결과와 미확인 항목](docs/validation.md)
- [F2001 수집 명령·저장 형식·실제 검증 방법](docs/f2001.md)
- [F2002 dry-run·색인·재실행 검증·비용 요인](docs/f2002.md)

## 실행

Python 3.10+ (검증: 3.12), 저장소 루트에서 실행합니다.

```bash
cd /workspace/dart_insight
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
cp .env.example .env
```

`.env`의 `DART_API_KEY`에 본인의 OpenDART 키를 로컬에서 입력하세요.
또는 프로세스 환경변수/Streamlit Secrets로 주입하세요. 키를 채팅·소스·커밋에 넣지 마세요.
F2002 로컬 임베딩·검색·dry-run에는 API 키가 필요하지 않습니다. LLM 키는 후속 생성 단계에서만 사용합니다.
Streamlit Secrets 예제는 `.streamlit/secrets.toml.example`입니다.

```bash
# 실제 호출 확인: 캐시를 무시하고 기업코드와 첫 페이지 조회
python -m dart_insight.cli --company 삼성전자 --begin 2025-01-01 --end 2025-12-31 --type A --refresh
python -m dart_insight.cli --company 현대자동차 --begin 2025-01-01 --end 2025-12-31 --type A --refresh
python -m dart_insight.cli --company 금양 --begin 2025-01-01 --end 2025-12-31 --type A --refresh

# F2001: 세 기업, A~J 전체 유형·전체 페이지, 부족하면 과거로 기간 확대
python -m dart_insight.collect --begin 2025-01-01 --end 2025-12-31 --refresh

# F2002: 입력 검증만 수행 (실제 collection.json 경로 지정, API 키 불필요)
python -m dart_insight.index_metadata --input .local/data/metadata/run-7kvbb30c/collection.json --dry-run

# 로컬 임베딩: 첫 실행은 BGE-M3 가중치 다운로드 필요, 기존 OpenAI DB와 다른 경로 사용
python -m dart_insight.index_metadata --input .local/data/metadata/run-7kvbb30c/collection.json --db-dir .local/data/chroma_bge_m3 --batch-size 16
# 같은 명령을 재실행하여 after.total 불변·count_delta=0 확인

# 같은 로컬 모델로 메타데이터 검색 (전체 RAG는 아직 미구현)
python -m dart_insight.search_metadata --db-dir .local/data/chroma_bge_m3 --query "삼성전자 사업보고서" --top-k 5

# 초기 연결 확인 화면
streamlit run app.py

# 실제 API/키 없이 실행하는 단위 테스트
python -m unittest discover -s tests -v
```

기존 `dart_insight.cli`는 기본 1페이지(최대 100건)만 확인합니다. `--max-pages N`으로 제한을 바꿀 수 있으며
이 명령은 “100건 확보·색인 완료”를 의미하지 않습니다. 빈 결과(status 013)와 오류를 구분합니다.
네트워크/인증 오류가 발생하면 실패 종료하며 가상 공시로 대체하지 않습니다.
기업코드·목록 성공 응답은 `.local/data/cache/`에 저장됩니다. `--refresh`는 캐시를 무시합니다.

새 `dart_insight.collect`는 모든 페이지를 조회합니다. 고유 100건 이상이고 세 기업 모두 자료가
있을 때만 종료 코드 0·`status=complete`를 반환합니다. 부족하면 이전 1년 구간씩 추가하며
기본 하한은 2000-01-01입니다. `--types A`로 정기공시만 수집하거나 `--earliest`로 하한을 바꿀 수 있습니다.
최종 건수·조회 기간·예시 텍스트와 저장 위치를 JSON으로 출력합니다.
실행별 결과는 `.local/data/metadata/run-*/collection.json`에 별도로 저장되어 이전 결과를 덮어쓰지 않습니다.
하한까지 수집해도 부족하면 `insufficient_data`를 저장하고 종료 코드 2, API/설정/저장 오류는 1입니다.

필요 네트워크: 설치 시 `pypi.org`, `files.pythonhosted.org`, 수집 시 `opendart.fss.or.kr`,
사용자용 원문 링크 `dart.fss.or.kr`. 최초 모델 다운로드는 Hugging Face와 배포 CDN 접근이 필요합니다.
모델을 완전히 캐시한 후 `HF_HUB_OFFLINE=1`로 실행하면 네트워크 없이 임베딩·검색할 수 있습니다.
향후 GMS LLM 엔드포인트는 `https://gms.ssafy.io/gmsapi/`이며 현재 LLM 연결은 미구현입니다.
클라우드 환경의 네트워크/키 초안 저장과 실제 런타임 적용·게시 여부는 별개입니다.

## 현재 구현 파일과 요구사항

| 파일 | 구현 범위 |
|---|---|
| `dart_insight/config.py`, `.env.example`, `.streamlit/secrets.toml.example` | F2012 기반 설정·키 누락 안내, NF2003·NF2004 |
| `dart_insight/data/models.py` | F2001 기반 기업/공시 모델·색인 텍스트 |
| `dart_insight/data/cache.py` | F2001 수집 기반, NF2002의 API 응답 캐시 부분 |
| `dart_insight/opendart/client.py` | F2001 기반 공식 기업코드·유형별 공시 목록·페이지 순회, NF2006 |
| `dart_insight/data/collection.py`, `dart_insight/collect.py` | F2001 전체 페이지 수집·기간 확대·접수번호 중복 제거·색인 텍스트·실행별 저장·건수 보고 |
| `dart_insight/vectorstore/metadata.py`, `dart_insight/index_metadata.py` | F2002 입력 검증·단일 컬렉션 upsert·기업 필터·변경 없는 문서 생략·건수 보고 |
| `dart_insight/cli.py`, `app.py` | 연결 검증 진입점, F2012의 초기 진행·오류·금융 면책 안내 |
| `rag/prompts`, `services`, `ui`의 `__init__.py` | F2003~F2012 후속 모듈의 경계만 표시, 기능 미구현 |
| `tests/test_*.py` | 초기 연결·설정·캐시 로직 테스트. 실제 공시 수집이나 전체 요구사항 검증 아님 |
| `docs/*.md` | 전체 필수 요구사항 계획·설계·검증 상태 |
| `requirements.txt`, `requirements.lock.txt`, `.python-version`, `.gitignore` | 지정 스택·재현 설치·로컬 데이터 제외 |

이 서비스는 공시 근거 기반 정보 제공 목적이며 투자 조언이 아닙니다.
답변은 금융·투자 전문가의 조언을 대신하지 않습니다.
