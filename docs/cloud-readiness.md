# Q-11/Q-12 안정화와 Community Cloud 준비

## 확인된 원인과 범위

실제 실패 GMS 응답/키가 없어 Windows 장애의 정확한 원인은 미확정이다.
기존 코드는 JSON 파싱·envelope·스키마·인용 검증 오류를 모두 같은 메시지로 처리하고 동일 요청을 반복했다.
현재는 완전한 Markdown JSON 코드블록/문자열 text 블록만 손실 없이 정규화한다.
length 종료는 JSON이 문법상 정상이어도 거절한다. refusal/모델 구조 불일치도 자동 재시도하지 않는다.
JSON 구문·빈 응답·스키마/근거 오류는 원문을 재전송하지 않는 교정 지시로 최대 2회 재시도한다.
HTTP 429/500/502/503/504는 답변 경로에서 최대 2회 backoff, 나머지 HTTP/통신 오류는 즉시 실패한다.
검색어 경로는 기존 HTTP 즉시 실패 정책을 유지한다. 잘못된 인용·알 수 없는 엔티티를 정상 답변으로 수용하지 않는다.
max_completion_tokens 기본 4096은 gpt-4.1-mini용 출력 예산이며 모델/GMS별 실제 호환성은 미검증이다.
다른 API(Responses 등) envelope는 추측 변환하지 않는다. 400이면 모델·지원 옵션을 확인하고 재시도하지 않는다.

## 계측과 중복 방지

앱 서버 콘솔의 `stage=... seconds=... status=...`를 확인한다. payload/질문/답변/키/원시 예외는 기록하지 않는다.
GMS에는 HTTP 상태·분류·시도·안전한 종료 사유·숫자 토큰 수만 기록한다. Cloud 서버 로그도 접근 권한을 제한한다.
단계: opendart_download, xml_parse, document_collection, chunking, bge_model_load, embedding,
chroma_upsert, index_and_verify, rag_retrieval, gms_query_http/validation, gms_http/validation.
index_counts에는 전체/변경/재사용 청크, document_reuse에는 본문 재사용 여부가 있다.
포함 관계인 단계의 시간은 합산하지 않는다. embedding 시간에는 최초 모델 로딩이 포함될 수 있다.

원장은 이미 성공한 본문을 재사용하며, 임베딩은 기존 내용·메타데이터가 같은 청크를 재사용한다.
추가로 요청 내 같은 공시의 반복 청킹·Chroma 대조를 제거했다. 다음 요청에서는 DB를 다시 검증하므로
삭제/손상/청크 설정 변경을 원장 완료 상태만으로 정상 처리하지 않는다.
색인 중 실패하면 완료로 전환하지 않고, 다음 시도에서 검증된 청크를 재사용한다.
기존 SHA 기반 본문/청크 설정 ID 및 모델·1024차원·정규화 프로파일 검사를 유지한다.
모델 객체는 st.cache_resource가 보관한 embeddings에 지연 로딩되며 동시 최초 로딩도 잠금으로 보호한다.
CPU microbatch 기본 4(이전 16), EMBEDDING_BATCH_SIZE=1~64로 조정 가능.
한 번에 계산하는 입력 수만 줄이며 청크/검색 수/벡터 프로파일/텍스트를 자르지 않는다.
실제 BGE CPU 처리 속도·peak RAM·배치별 수치 오차/검색 품질은 이번 환경에서 측정하지 않았다.
원문 크기 제한을 줄이거나 일부 공시를 자동 생략하지 않는다.

## 합성 성능 비교 (실제 API/모델 측정 아님)

Linux Python 3.12, 단일 실행, 합성 한국어 본문 417청크, 실제 임시 Chroma/SQLite/LangGraph,
HTTP·GMS·BGE는 테스트용 구현. 기준 eed60e5의 RagService와 새 구현을 같은 배치 4로 비교했다.
운영 경로는 실제 API/모델을 그대로 사용하며 이 실험만 외부 경계를 대체했다.

| 경로 | 최초 분석 | 동일 질문 재분석 | 재분석 원문 다운로드/문서 임베딩 |
|---|---:|---:|---|
| 기존 | 13.9662초 | 0.1488초 | 0 / 0 |
| 수정 | 12.5370초 | 0.0825초 | 0 / 0 |

재분석에는 질문 벡터 1개 계산이 남는다. 최초 문서 417청크와 후보/질문 벡터를 포함해 420개 입력을 계산했다.
최초 시간은 실제 Chroma 저장/검증이 포함되며 초기화·OS 캐시 차이가 있어 일반적인 속도 향상률로 해석하지 않는다.
실제 Windows 사업보고서 다운로드/XML/BGE 최초 로딩·임베딩·GMS 시간 및 peak RAM은 **미검증**이다.
CPU BGE가 사용자 환경의 주 병목인지도 새 단계 로그로 확인해야 한다.
재현: 저장소 루트에서 `python scripts/benchmark_rag.py` (Linux 개발용, 별도 임시 DB만 사용).

## 무료 배포 판정: 조건부 검토, 실서비스 가능 확정 불가

배포 실행 없음. Python은 Cloud Advanced settings에서 **3.11**을 명시해 Windows 기준과 맞춘다.
현재 Linux 3.12에서 고정 requirements 설치 환경의 pip check는 정상이며 3.11 신규 설치는 미검증이다.
requirements의 torch CPU 인덱스를 유지한다. 별도 Netlify 프런트엔드는 만들지 않는다.

2026-10-22 Hugging Face 공식 API 메타데이터 조회:
`https://huggingface.co/api/models/BAAI/bge-m3?blobs=true`, revision
`5617a9f61b028005a4858fdac845db406aefb181`, pytorch_model.bin **2,271,145,830 bytes**.
이는 한 가중치 파일의 크기이며 전체 모델 다운로드/설치 디스크 용량은 더 크다.
CPU float32 로딩·추론에는 가중치 외 tokenizer/PyTorch/activation/Chroma/앱 RAM이 추가로 필요하다.
사용자의 1.57GB 관측만으로 최대 메모리 요구량을 판단할 수 없다.

현행 Community Cloud CPU/RAM/disk 한도는 공식 문서 네트워크 접근 실패로 확인하지 못했다.
[공식 배포 문서](https://docs.streamlit.io/deploy/streamlit-community-cloud) 및 배포 시 앱의 실제 한도/메트릭을 확인해야 한다.
과거의 무료 RAM 수치를 현재 보장값으로 제시하지 않는다. BGE-M3를 온전히 유지하는 이 구성은
가중치만 약 2.27GB이므로 무료 환경 메모리 부족·다운로드 timeout·재시작의 실질적 위험이 있다.
배치를 낮춰도 모델 자체 RAM은 사라지지 않는다. 무료 환경에서 성공한다고 보장하지 않는다.

최소 대안은 로컬에서 BGE-M3로 메타데이터와 데모 대상 실제 공시 본문을 미리 색인해 배포 시 작업량을 줄이는 것이다.
새 질문 임베딩에도 BGE 모델은 여전히 필요하므로 메모리 문제를 완전히 해결하지는 못한다.
실제 한도가 부족하면 추가 메모리 호스팅 또는 별도 BGE 추론 서비스를 검토해야 한다.
후자는 구조/운영 범위 변경이므로 이번에는 구현하지 않았다. 임의로 다른 모델/가짜 데이터로 교체하지 않는다.

## 초기 데이터와 Secrets

`.streamlit/secrets.toml.example`의 빈 키를 Cloud Secrets UI에 실제 값으로 입력한다.
환경변수 > Streamlit Secrets > 로컬 .env 순서. .env와 secrets.toml은 gitignore 대상이다.
키나 DB를 공개 저장소에 올리지 않는다. 로컬 1,065건은 Cloud에 자동 전송되지 않는다.
초기 빈 DB는 앱이 색인 없음으로 안내하며 자동으로 전체 수집/모델 다운로드를 실행하지 않는다.

로컬에서 공식 collect → index_metadata CLI로 실제 메타데이터를 준비한다(README 명령 참조).
기존 사용자 DB가 아닌 **별도 준비 경로**에서 생성하고 메타데이터 1,065건 등 기대 수를 확인한다.
배포 준비 산출물은 사용 허용된 실제 collection.json과 Chroma DB의 검증된 스냅샷이며 키/원장 개인정보는 제외한다.
스냅샷은 앱/수집을 종료해 SQLite WAL이 반영된 일관된 상태에서 복사하고 같은 패키지 버전으로 검증한다.
Cloud에 쓸 안전한 전달 경로·스냅샷 크기/이용 조건은 배포 승인 전 결정해야 한다.
이번에는 공개 DB 커밋이나 자동 외부 다운로드/초기화 코드를 추가하지 않았다. 따라서 빈 Cloud 인스턴스에서
즉시 검색 가능하다고 주장하지 않는다. 초기 데이터 전달이 남은 배포 장애 요인이다.

기본 `.local/data/chroma_bge_m3`, `collection.sqlite3`, `models`는 Cloud 로컬 파일시스템이다.
재실행에서 남을 수 있어도 재부팅/재배포/휴면 복구 후 영속성을 보장하지 않는다.
외부에 복구 가능한 초기 산출물을 보관하고 새 인스턴스에 복원하는 절차가 필요하다.
시작마다 무조건 DB 생성/덮어쓰기/전체 재색인을 실행하지 않는다. 세션 채팅 기록은 기존처럼 세션 한정이다.

## Windows 실제 재검증

```powershell
git pull --ff-only origin feat/required-rag-integration
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m streamlit run app.py
```

서버 콘솔을 보존한 채 삼성전자 사업보고서 분석을 처음 실행하고 단계별 시간·청크 수·GMS 오류 분류를 기록한다.
동일 공시/동일 분석을 다시 실행해 document_cache reusable_documents>0, changed=0, bge_model_load 재발생 없음,
다운로드 없음과 GMS 호출 시간을 비교한다. 후속 질문에서도 기존 본문과 임베딩 재사용을 확인한다.
GMS 오류라면 category/HTTP 상태/토큰 수만 공유하고 원문 응답/키/.env를 출력하지 않는다.
XML 진단·공시별 기록 복원·분석/채팅 분리·인용 링크·복사 동작도 재검증한다.

배포 승인 후에는 Secrets 및 별도 초기 데이터 복원 → 모델 cold start RAM/시간 측정 → 기업/공시 검색 →
실제 분석/후속 질문/출처 확인 → 재분석 재사용 → 서버 재시작 후 복구 여부 순으로 검증한다.
OOM/재시작 반복이면 배포 성공으로 판정하지 않는다.

실제 200/stop 검증 실패의 field/rule별 후속 로그: [gms-validation-diagnostics.md](gms-validation-diagnostics.md).

검색어/답변 공통 HTTP 정책과 검색어 전용 fallback의 최신 절차: [gms-recovery.md](gms-recovery.md).
