# 전체 파이프라인 통합 안정화 및 실제 ZIP 검증

## 실제로 재현한 원인

사용자 첨부 `document-20251203800556-eeee84f646bc4400be391700961b6218.zip`에 직접 접근해 검사했다.
ZIP 2,962 bytes, XML 확장자 멤버 1개/14,876 bytes, SHA-256:
`78c132fa11294864063b190062d0c85c0da4b1f1c294635083c43492cc177f41`.
파일 내용은 UTF-8 HTML이며 금양 단기차입금증가결정(자율공시) 본문이다.
HTML meta에는 euc-kr가 적혀 있으나 실제 bytes는 UTF-8로 해석된다. XML encoding 선언은 없다.

수정 전 엄격 파서에서 **73행 3열** 오류를 재현했다. XML이 아닌 HTML의 `<meta>` 2개를 닫지 않아
`</head>`에서 태그 불일치가 발생하며, 본문의 `<br>` 12개도 HTML 빈 요소 표기였다.
제어문자나 대용량 제한이 원인은 아니다. 기업/접수번호를 조건으로 분기하지 않는다.

HTML 루트에만 표준 빈 요소 14종의 닫힘 문법을 보정한다. 정상 XML 태그와 명시적으로 닫힌 요소는 보존한다.
주석/CDATA 안의 가짜 종료 태그는 구조 판정에 사용하지 않는다. 그 뒤 **전체 엄격 XML 재파싱**을 수행한다.
일반 HTML recover 파서로 잘못된 중첩을 임의로 닫거나 누락된 문단을 묵인하지 않는다.
HTML body가 단일일 때만 표시 본문을 추출하고 head/style/script는 비본문으로 명시해 제외한다.
비본문 제외와 빈 요소 보정은 진단·원장 parse_notes·RAG audit·UI에 유지한다.

실제 ZIP 결과:

- input_format=html, html_void_repairs=14, excluded_noncontent_elements=2(head/STYLE).
- status=ok, xml_files_checked=1, xml_files_dropped=0, text_characters=1505.
- 별도 표준 HTMLParser가 읽은 **표시 본문 전체**와 추출 결과를 동일한 공백 정규화 후 문자열 전체 비교해 일치.
  정정 전후 항목/단기차입 실행일/금액/표 내용/관련 공시 텍스트를 보존했다. CSS는 검색 본문에 넣지 않는다.
- 실제 ZIP을 테스트 fixture로 보존하고 해시/원래 오류 위치를 검사한다. 공개 공시이며 키나 로컬 DB는 포함하지 않는다.
- 실제 원문→파서→SQLite→Chroma→LangGraph→GMS JSON/인용 검증 경로를 통합 테스트했다.
  이 중 HTTP/LLM/BGE는 테스트 대역이므로 **실제 API 성공/실제 검색 품질 검증이 아니다**.

## 함께 발견해 수정한 결함

1. 외부 목록 캐시가 기업·조회 기간과 무관한 단일 목록이었다. 같은 소스 인스턴스에서 기업/기간을 바꾸면
   이전 기업 결과를 사용할 수 있었다. `(corp_code, begin, end)`로 분리하고 응답 기업도 검증한다.
2. 복수 XML 중 빈 본문을 건너뛰고 다른 멤버만 정상 결과로 반환했다. 이제 빈 멤버도 오류로 기록하고
   전체 XML 검사는 계속하되 전체 반환/저장은 거절한다.
3. XML encoding 선언의 strict decode 실패 뒤 다른 charset으로 재해석할 수 있었다.
   명시된 XML encoding은 신뢰 가능한 유일한 decoder로 사용하며 실패 시 encoding 오류를 낸다.
   BOM 우선과 선언 없는 OpenDART UTF-8/EUC-KR/CP949 지원은 유지한다. HTML meta를 XML 선언처럼 강제하지 않는다.
4. 허용되지 않는 XML 제어문자/코드포인트는 삭제하지 않고 line/column/codepoint로 진단한다.
   깊은 HTML의 가시 텍스트 추출은 반복 순회로 구현해 Python 재귀 깊이 오류를 피한다.
5. 후보/수집/저장 원문의 공시·기업 식별자를 색인 전에 검증한다. 과거 잘못 저장된 기업의 본문도
   새 색인으로 정상화하지 않고 보존한 채 오류로 중단한다.
6. UI 오류에 마지막 처리 단계를 함께 표시한다. 실패한 재분석이 이전 정상 분석을 지우지 않는지도 AppTest로 확인했다. 응답 원문·키·예상 밖 예외 문자열은 표시하지 않는다.

ZIP 100 MiB/최대 1,000 XML/실제 읽기 크기/경로 비추출/엔티티 선언 거절/원격 DTD 비조회 제한은 유지했다.
부분 추출을 저장하지 않고 기존 정상 원장·다른 공시 색인을 보존한다. 기존 DB 스키마/의존성/모델을 변경하지 않았다.

## 자동화 검증 범위

| 대상 | 입력 및 검증 |
|---|---|
| 실제 공시 | 첨부 금양 ZIP 오류 위치·전체 표시 본문 보존·14개 구조 보정·실제 DB/그래프 연결 |
| 삼성전자 사업보고서 | 이전 사용자 제공 제목/앰퍼샌드 발췌 + 대형 합성 fixture. 실제 전체 ZIP은 없음 |
| 현대자동차 분기보고서 | 짧은 합성 fixture. 기업코드/기간 메타데이터는 테스트용이며 공식 조회 결과가 아님 |
| 다중 멤버/형식 | 대형 본문+실제 HTML+짧은 EUC-KR XML, UTF-16 BOM, 비XML 첨부는 읽거나 실행하지 않음 |
| 손상 입력 | 제어문자, 잘못된 중첩/CDATA/엔티티/숫자 참조, 빈 멤버, 잘못된 선언 encoding, HTML body 누락 모두 거절 |
| 보존 입력 | 정상 Unicode 태그/속성/CDATA/주석/기존 엔티티/명시적 빈 요소 종료와 깊은 중첩 |
| 기업 전환 | 같은 서비스/소스로 3기업·3유형 fixture 전환, 후보/원문/검색 결과/인용의 항목 격리 |
| 재사용 | 재분석 다운로드 0/문서 재임베딩 0/질문 벡터 1, 색인 수 유지 |
| 실패 복구 | XML 실패→원장 pending/이전 기업 색인 유지→정상 ZIP 재시도→indexed |
| GMS 경계 | 정상 코드블록 JSON, 429 후 정상, 401 즉시 중단·복구, 허위 인용 거절 후 재시도, supported=false 근거 부족 |
| 기존 GMS 회귀 | 401/403/404/429/5xx/timeout/network·총 재시도 예산·검색어 fallback·스키마/자료형/실제 청크 ID 검증 |

전체 **176개 통과 / 실패 0 / skip 0**, 20.130초. 기존 165개에 신규 11개 추가.
Linux Python 3.12.14, 실제 임시 Chroma/SQLite/LangGraph 및 Streamlit AppTest, pip check 정상.
Windows 파일 핸들 격리를 위해 DB 통합 검증은 별도 Python 프로세스에서 실행 후 종료한다.

## Windows 로컬 재검증

기존 서버를 종료하고 로컬 변경을 보존한 뒤 갱신한다. .env/DB를 지우거나 기존 파일을 덮어쓰지 않는다.

```powershell
git status --short
git pull --ff-only origin feat/required-rag-integration
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m dart_insight.inspect_document --input tests/fixtures/opendart/20251203800556.zip
.\.venv\Scripts\python.exe -m streamlit run app.py
```

검사 결과의 status=ok, html_void_repairs=14, text_characters=1505, xml_files_dropped=0을 확인한다.
실제 진단 ZIP 원본 경로를 --input으로 지정해도 같은 결과여야 한다.
본문을 파일로 확인하려면 --text-output에 **새 경로**를 추가한다(기존 파일 덮어쓰기 거절).

삼성전자 사업보고서→현대자동차 분기보고서→금양 첨부 공시 순서로 분석/채팅을 실행한다.
공시마다 출처와 기록이 분리되고 복귀 시 분석이 불필요하게 재실행되지 않는지 확인한다.
같은 분석 재실행 시 원문/색인 재사용을 서버 로그에서 확인한다.
XML 보정 안내와 표의 실행일/금액을 원문과 비교한다. 최종 GMS 오류는 stage/category/field/rule만 확인한다.
실제 API/모델의 응답 시간·BGE 검색 품질·Windows RAM/브라우저 동작은 이 로컬 절차에서 확인해야 한다.

## 남은 범위와 한계

- 이 환경에서 OpenDART/GMS 키 미설정을 값 노출 없이 확인했다. 실제 API 호출·BGE 추론·Windows Python 3.11/브라우저·사용자 1,065건 DB 직접 검증은 미수행.
- 이전 실제 GMS 200/stop 검증 오류의 구체 규칙은 새 로그/실제 응답 없이 확정할 수 없다.
  근거 검증은 유지하며 [GMS 복구 정책](gms-recovery.md)을 따른다.
- 삼성전자 전체 ZIP이 없어 그 3개 실제 XML 전체 무손실/파싱 성공은 주장하지 않는다.
- 일반 HTML의 생략된 종료 태그, 비정상 속성, 알려지지 않은 엔티티 등 구조 추정이 필요한 형식은
  여전히 엄격하게 실패한다. 모든 실제 공시 형식을 지원한다고 보장하지 않는다.
- 이 버전은 모든 XML 멤버의 본문을 요구한다. 빈 XML이 정당한 부속 문서인 새 형식은 별도 명세 확인이 필요하다.
- 실제 배포/master 병합/고급 기능 추가 없음. 기존 사용자 데이터 삭제·덮어쓰기 없음.
