# GMS 전체 경로 오류 분류·재시도·검색어 fallback

이 문서는 6275a7f 이후의 통합 안정화 정책이다. 예전 문서의 ‘검색어 HTTP 즉시 실패/네트워크 재시도 없음’보다 우선한다.
실제 Windows 오류에 HTTP 상태가 포함되지 않았으므로 이번 장애의 특정 원인을 확정하지 않는다.
외부 API 키/응답이 없어 실제 GMS 재현·성공은 미검증이다.

## 공통 요청 정책

검색어 추출과 후보 선별·답변 생성은 같은 post_chat 경로로 요청한다.
각 호출은 HTTP/통신/출력 오류를 합쳐 **최대 3회**(처음 + 2회)만 시도한다. 경로를 중첩해 9회 재시도하지 않는다.
재시도 간 backoff는 1초, 2초이며 기존 호출 간격도 유지한다. 요청별 timeout 설정을 유지한다.

| 실패 | 코드 | 정책 |
|---|---|---|
| HTTP 401/403 | authentication | 즉시 실패, 인증/모델 접근 권한 확인 |
| HTTP 404 | not_found | 즉시 실패, 모델 이름/엔드포인트 확인 |
| HTTP 429 | rate_limit | 제한적 backoff 후 실패 |
| HTTP 5xx | server | 제한적 backoff 후 실패 |
| timeout | timeout / exception=Timeout | 제한적 backoff 후 실패 |
| 연결/기타 requests 오류 | network / 정적 예외 타입 | 제한적 backoff 후 실패 |
| HTTP 400/리다이렉트/기타 | request | 즉시 실패, 모델/지원 옵션 확인 |
| 빈 출력/JSON 구문/스키마/인용 | 기존 검증 분류 | 예산 안에서 교정 재시도 |
| 잘린 응답/거절/envelope 변경/내부 검증 오류 | 기존 검증 분류 | 추측 변환 없이 실패 |

모든 응답을 닫으며 예외 메시지·원문 응답·URL·headers·키는 로그에 넣지 않는다.
로그에는 stage, HTTP 상태, category, attempt, 안전한 exception 타입이 포함된다.
공급자 오류 본문은 message를 출력하지 않는다. 고정 allowlist의 code만 기록한다:
invalid_api_key, model_not_found, insufficient_quota, rate_limit_exceeded, unsupported_parameter,
invalid_request_error, permission_denied, context_length_exceeded.
param도 model/response_format/max_completion_tokens/messages만 허용한다. 알 수 없는 값은 None이다.
예: request HTTP 400 code=unsupported_parameter parameter=max_completion_tokens라면 해당 모델/GMS의 옵션 호환성을 확인한다.
공급자 실제 응답을 보기 전 옵션을 임의로 제거하거나 다른 모델로 바꾸지 않는다.

## 검색어에만 적용하는 fallback

QueryExtractionError 후 원래 질문(앞뒤 공백 제거)과 선택 공시 접수번호를 검색어로 사용한다.
진행 화면과 결과 audit.query_fallback(category/http_status), 저장된 분석·채팅 결과 경고에 사용 사실을 표시한다.
LLM 검색어 추출 성공으로 캐시하지 않아 다음 실행에서는 정상 추출을 다시 시도한다.
수집/색인 원장과 이미 검증한 본문은 그대로 재사용한다. DB 스키마를 바꾸거나 과거 데이터를 덮어쓰지 않는다.

후속 후보 선별·원문 검증·LangGraph 검색·최종 답변의 스키마/인용/근거 검증은 그대로 실행한다.
fallback은 LLM 답변을 생성하거나 출처를 조작하지 않는다. 잘못된 인용은 여전히 거절한다.

같은 API 키/모델의 401/403/404/400 계열 오류가 검색어 단계에서 확인되면 동일 Runtime의 후속 GMS 호출은
추가 HTTP 요청 없이 구체적인 설정 오류로 중단한다. 검색어 fallback으로 인증을 복구할 수는 없다.
설정 수정 후 새 실행은 정상 요청을 다시 할 수 있다. GmsClient에서 새 extract 시작 시 이전 차단 상태를 초기화한다.
GMS 키 미설정 등 ConfigurationError와 잘못된 사용자 입력은 불필요한 네트워크 작업 전에 안내한다.
검색어 추출의 일시 실패는 정상 최종 답변까지 이어질 수 있지만, 최종 GMS도 실패하면
‘후보 선별 실패’ 또는 ‘최종 답변 생성 실패’를 별도로 표시한다. 최종 답변 fallback은 없다.

## Mock 및 통합 검증

정상 응답, 401/403/404/400/302 즉시 실패, 429·5xx·timeout·network 복구와 소진,
HTTP+JSON 혼합 실패의 총 3회 한도, API 키/응답/임의 필드 로그 비노출, 설정 오류 중복 호출 중단/복구를 검증한다.
기존 잘못된 JSON·스키마·인용/실제 청크 ID 매핑 테스트를 유지한다.
검색어 timeout/network/rate_limit/server/json_syntax/schema/envelope/empty_content fallback 후
실제 임시 Chroma/SQLite/LangGraph와 테스트 LLM/임베딩으로 답변·근거 연결을 확인한다.
허위 인용은 fallback에서도 거절되고, 후속 정상 추출이 회복되며 본문 다운로드는 재실행하지 않는다.
UI 경고가 rerun에도 유지되고 자동 분석이 불필요하게 재실행되지 않는지 AppTest로 검증한다.
이 검증은 실제 GMS/BGE 호출 성공이나 Windows 브라우저 실검증이 아니다.

## Windows 재검증

```powershell
git pull --ff-only origin feat/required-rag-integration
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m streamlit run app.py
```

1. 새 질문(기존 검색어 캐시에 없는 질문)으로 분석/채팅을 각각 실행한다.
2. 서버 콘솔의 gms_query_http HTTP/category/exception/attempt와 gms_query_validation을 확인한다.
3. 일시 실패 시 최대 3회 후 원래 질문 fallback 안내가 나오고 후보 선별/검색/답변 단계로 이어지는지 확인한다.
4. 인증 오류는 반복 호출되지 않고 설정 안내로 중단되는지 확인한다. 키를 수정한 뒤 앱을 다시 실행한다.
   실제 키를 고의로 바꿔 오류를 만들 필요는 없으며 실패 주입은 Mock 테스트로 검증한다.
5. 답변 실패는 gms_http 또는 gms_validator의 field/rule로 구분한다. 원시 응답이나 키를 출력하지 않는다.
6. 정상 답변의 인용 링크·청크·공시별 기록·분석/채팅 분리·XML 전체 검증·기존 색인 재사용을 확인한다.

네트워크/키 제한, 미지원 모델·옵션, 실제 한도 초과는 코드만으로 해소할 수 없다.
이 경우 안전한 분류 코드와 HTTP 상태만 확인하고 동일 실패를 무한 재시도하지 않는다.

검증 결과: Linux Python 3.12.14에서 전체 165개 통과 / 실패 0 / skip 0, 13.879초. pip check 정상. 기존 157개에 신규 8개 추가. 실제 API/Windows 성공을 의미하지 않는다.
