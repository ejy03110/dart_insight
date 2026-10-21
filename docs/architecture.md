# 프로젝트 구조·데이터 및 RAG 설계

## 현재 구조

```text
dart_insight/                    # 현재 연결된 저장소
├── app.py                      # 초기 Streamlit 연결 화면
├── requirements.txt            # PDF 지정 직접 의존성
├── requirements.lock.txt       # 검증 환경 전이 의존성 고정
├── .python-version
├── .env.example
├── .streamlit/secrets.toml.example
├── dart_insight/
│   ├── config.py               # 비밀값 비노출 설정·검증
│   ├── cli.py                  # 읽기 전용 연결 확인
│   ├── collect.py              # F2001 세 기업 일괄 수집 CLI
│   ├── index_metadata.py       # F2002 dry-run/명시적 임베딩 CLI
│   ├── data/
│   │   ├── models.py           # 기업/공시·1건 색인 텍스트
│   │   ├── collection.py       # F2001 기간 확대·중복 제거·실행별 결과 저장
│   │   └── cache.py            # 원자적 JSON 캐시
│   ├── opendart/client.py      # 기업코드·공시 목록
│   ├── vectorstore/metadata.py # F2002 단일 컬렉션·입력 검증·upsert
│   ├── rag/prompts/            # 후속: 3개 프롬프트·그래프
│   ├── services/               # 후속: 요약·조건 탐색
│   └── ui/                     # 후속: 선택·대화·근거 UI
├── tests/                      # API 호출을 대체한 단위 테스트; 실제 수집 증거 아님
└── docs/                       # 요구사항·설계·출처·검증 결과
```

## 수집 흐름

1. `corpCode.xml` ZIP을 메모리에서 열고 XML을 안전하게 파싱한다. 종목코드로 세 기업을 식별한다.
2. `list.json`을 기업·기간·공시유형(A~J)별로 호출하고 `total_page`까지 순회한다.
   기존 연결 확인 CLI/화면은 1페이지 제한이다. F2001 `collect` CLI는 `max_pages=None`으로 전체 조회한다.
3. 목록 API는 항목별 `pblntf_ty`를 반환하지 않으므로 유형을 제목에서 추정하지 않는다.
   유형별 조회 매개변수를 정규화된 `disclosure_type`으로 기록한다. 공시 분류는 정기공시 등 상위 분류다.
4. `rcept_no`로 중복 제거, 항목당 색인 텍스트 1개 구성. 정정공시는 고유 접수번호를 유지하며 임의로 합치지 않는다.
5. F2001 수집기는 한 구간의 모든 기업·유형·페이지를 마친 뒤 고유 100건과 기업별 1건 이상을 검사한다.
   부족하면 이전 1년 구간을 경계 중복 없이 추가하며 하한까지 모자라면 미완료 처리한다.
   원본 API 응답은 기존 캐시에, 정규화 메타데이터·색인 텍스트·기간·집계는 실행별 JSON에 저장한다.
   사용자 키는 저장하지 않는다. 사용자 로컬에서 1,065건 확보를 보고받았으나 Codex에 실제 파일은 없다.

현재 캐시: `.local/data/cache/corporations/<hash>.json`, `disclosures/<hash>.json`.
키는 비밀값을 제외한 정규화 요청 매개변수 SHA-256이며 본문에는 응답과 수집시각을 저장한다.
임시파일+원자적 교체로 반쪽 쓰기를 막는다. API 오류는 캐시하지 않는다.
013(조회 없음)은 정상 빈 결과로 캐시한다. TTL 만료·refresh 시 새 호출을 한다.
이 TTL 캐시는 F2004/NF2002의 “이미 수집·색인한 검색어는 재수집하지 않음”을 완성하지 않는다.

## 저장 구조

```text
.local/data/                   # Git 제외; 키는 이곳에도 저장하지 않음
├── cache/                     # 현재 구현: 기업코드·목록
├── metadata/run-*/collection.json # 현재: 실행별 정규화 목록·index_text·요약·조회 구간
├── documents/<rcept_no>/      # 후속: 원본 ZIP/XML, 텍스트, 출처 manifest
├── collection.sqlite3         # 후속: 항목/검색어/문서 수집·색인 완료 원장
└── chroma/                    # 현재: PersistentClient의 dart_metadata. 원문 컬렉션은 후속
```

메타데이터 필드: `corp_code`, `corp_name`, `stock_code`, `report_nm`, `rcept_no`,
`rcept_dt`(ISO 날짜), `disclosure_type`, `source_url`, `collected_at`, `source_type`.
원문 다운로드 URL에는 키가 필요하지만 저장·화면용 출처 URL은
`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=<접수번호>`만 사용한다.
F2001 저장 결과는 JSON으로 다시 로드할 수 있으며 `disclosures` 배열에 고유 접수번호별 항목이 있다.
같은 공시가 여러 유형 조회에 나타나면 유형을 합쳐 보존하고 건수는 한 건으로 계산한다.
후속 원문은 API `document.xml` 응답 ZIP/XML에서 표 구조·문단·인코딩을 고려해 추출한다.
본문 자체가 프롬프트 명령으로 해석되지 않게 데이터 경계와 시스템 프롬프트를 분리한다.

## 두 컬렉션과 ID (메타데이터만 구현)

| 컬렉션 | 단위·ID | 주요 메타데이터 |
|---|---|---|
| `dart_metadata` | 공시 1개 = 청크 1개, `meta:{rcept_no}` | 위 공시 필드, `source_type=opendart_metadata` |
| `dart_documents` | `doc:{item_rcept_no}:{document_id}:{chunking_version}:{chunk_index}` | 관련 공시 접수번호, 원문 문서 ID, 제목, URL, 기업코드, 검색어, 수집일, 내용 hash, 분할 버전, `source_type=opendart_document` |

`document_id`는 원문 접수번호+ZIP 내 파일 식별자. 같은 문서를 여러 항목에서 사용해도
`item_rcept_no`로 구분된다. upsert로 중복을 방지하며 원문/분할 설정 변경 시 해당 항목·문서의
이전 청크를 정리한 뒤 교체해야 오래된 청크가 남지 않는다.
원장은 항목+정규화 검색어+출처로 완료 상태를 관리하고 문서 다운로드·색인이 성공한 후에만 완료 처리한다.
다운로드 캐시는 문서별로 공유하되 항목별 색인 참조는 별개다. 강제 갱신은 명시적 사용자 동작으로 둔다.

F2002 `load_metadata()`는 F2001 파일을 검증하고 `index_metadata()`는 주입한 임베딩으로
`dart_metadata`에 upsert한다. 기본 임베딩 함수는 비활성화한다. 같은 ID·텍스트·메타데이터는
건너뛰고 변경 문서만 갱신한다. 컬렉션 모델명을 검사하여 다른 모델 혼입을 차단한다.
저장 후 입력 전체 ID/내용을 읽어 확인하고 전체·corp_code별 건수를 출력한다.
dry-run은 DB·API를 사용하지 않는다. 기존 자료를 삭제하지 않는 병합 방식이다. 상세 `f2002.md` 참조.

청킹 기본값 1000자/중복 200자는 설명·표 맥락과 토큰 비용 사이의 초기 절충이다.
설정 변경 결과는 청크 수·근거 품질로 검증하며 지금 값이 최적이라고 단정하지 않는다.

## 세 프롬프트·그래프 (설계, 미구현)

1. 검색어 추출: 공시 분석 전문가 역할. 입력은 선택 공시 메타데이터+질문.
   출력 `{"queries":["..."]}`. 파싱 실패는 안내/제한 재시도, 선택 공시 접수번호는 프로그램에서 반드시 추가.
2. 후보 선별: 입력은 후보 ID·제목·가능한 공식 요약/메타데이터(본문 다운로드 전).
   출력 `{"selected_ids":["..."],"reasons":{"id":"..."}}`.
   존재하는 후보 ID만 허용. 선택 공시 자체는 LLM 제외 결정·길이 규칙을 무시하고 합집합에 포함.
3. 답변 생성: 검색 근거만 사용, 주요 주장에 `[근거 N]`, 출처 유형 구분,
   문서에 없으면 “문서에서 확인되지 않습니다”. 투자 권유/가격 예측 금지, 면책 병기.

LangGraph는 `START → retrieve → generate → END`의 두 노드로 구성한다.
수집·색인은 질문 처리 전 준비 서비스에서 수행한다. `retrieve`는 선택 메타데이터를 고정 포함하고,
원문 컬렉션을 선택 항목으로 필터링하여 질문 유사도 상위 `TOP_K`개를 얻는다.
두 컬렉션 결과를 병합한 후 문서·내용 해시로 중복 제거하고 안정적인 근거 번호를 부여한다.
메타데이터와 원문은 같은 항목이어도 서로 다른 출처 유형이므로 합쳐 없애지 않는다.
`generate` 출력의 인용 번호가 실제 근거 집합에 속하는지 검증하고, 실제 인용한 근거만 UI에 노출한다.
인용이 없으면 그 사실을 명시한다. 관련 공시·이전 정정본 비교는 해당 원문을 확보한 경우에만 확장한다.

## 배포

Streamlit Community Cloud에서 저장소 루트 `app.py`, Python 3.12, Secrets 사용을 기본으로 한다.
앱의 `requirements.txt`를 사용한다. 실제 Cloud 배포/새 인스턴스 검증은 아직 수행하지 않았다.
로컬 Chroma·캐시는 인스턴스 재시작/재배포 시 유실될 수 있다. 후속 단계에서 재수집·재색인 명령과
100건 준비 게이트를 제공하고, 캐시가 없으면 안내한다. 별도 DB 서버/스케줄러는 두지 않는다.
