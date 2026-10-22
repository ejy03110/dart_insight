# 실제 GMS 200/stop 응답 검증 실패 후속 진단

## 제공 로그에서 확정 가능한 범위

HTTP 200, finish=stop, completion 739/469 토큰으로 응답을 받았다.
기존 코드의 schema_or_grounding는 response_value의 JSON 해석이 성공한 뒤 validate 콜백에서 난 오류다.
따라서 해당 두 시도는 JSON 구문 오류·빈 본문·length 종료로 분류된 실패가 아니다.
이 로그만으로 누락 필드/자료형/추가 필드/잘못된 인용 중 하나를 확정할 수 없다.
실제 응답·API 키는 이 환경에 없으며 실제 API 재현 검증은 수행하지 않았다.

현재 자동 분석 질문은 취득 원인 질문이 아니므로 취득 목적 보호 규칙은 해당 분석 실패의 직접 후보가 아니다.
우선 후보는 필수 필드/자료형/추가 필드 또는 인용 누락·범위·표기 위반이다.
기존 래퍼가 일반 ValueError/KeyError/TypeError/IndexError까지 묶었으므로 내부 검증 오류 가능성도 분리해야 한다.

## 새 안전 로그

다음은 합성 예시이며 실제 GMS 응답을 추정한 것이 아니다.

```text
stage=gms_validator exception=AnswerValidationError category=schema field=supported rule=boolean_required attempt=1 evidence_count=5
stage=gms_validation status=200 category=schema attempt=1
```

| category | field | rule 의미 |
|---|---|---|
| schema | $ | object_required: JSON 객체 아님 / unexpected_fields: 추가 필드 |
| schema | answer 또는 supported | required_field: 필수 필드 누락 |
| schema | supported | boolean_required: 문자열 true 등 boolean 아님 |
| schema | answer | string_required / nonempty_string |
| citation | answer | citation_required: 인용 없음 |
| citation | answer | citation_number_out_of_range: 1~검색 근거 수 범위 밖 |
| citation | answer | citation_token_format: 잘못된 인용 토큰 |
| grounding | answer | acquisition_method_is_not_purpose / missing_purpose_acknowledgement |
| schema | selected_ids | array_required / string_items_required |
| citation | selected_ids | candidate_id_not_allowed: 실제 후보에 없는 접수번호 |
| validator_internal | $ | unclassified_validator: 예상 밖 일반 검증 예외, 자동 재시도 없음 |

AnswerValidationError와 SelectionValidationError는 ValueError 하위 클래스이며 기존 호출자와 호환된다.
일반 예외는 내장 클래스 이름만 기록하고 임의 하위 클래스는 OtherValidationError로 대체한다.
응답·필드 값·실제 잘못된 번호·미지의 추가 필드명·예외 메시지/traceback·키는 기록하지 않는다.
다음 Windows 실행에서 stage=gms_validator/gms_validation 두 줄의 코드만으로 해당 검증 규칙을 구분할 수 있다.
API 원문을 출력하거나 전달할 필요가 없다.

## 프롬프트와 인용 ID 점검

기존 프롬프트는 두 필드 JSON과 Markdown 제목/목록을 함께 요구했다. 코드상 확정적인 스키마 충돌은 없지만
제목을 별도 summary/risks 필드로 만들거나 citations를 추가할 여지가 있어 answer 문자열 내부에만 작성하도록 명확히 했다.
supported는 JSON boolean이며 문자열이 아니다. 추가 필드는 기존처럼 거절한다.

검색 결과는 프로그램이 enumerate(evidence, 1)로 number를 부여한다.
모델에는 실제 id와 number를 함께 보내되 출력 인용은 [근거 N]의 number를 사용한다.
검증 후 evidence[N-1].id를 출처에 연결하므로 청크 ID나 접수번호를 숫자 인용처럼 사용하면 안 된다.
프롬프트에 이 차이와 개별 인용 형식([근거 1] [근거 2])을 명시했다.
원래 허용되지 않던 그룹 인용/범위 밖 번호를 자동 치환하거나 허위 출처를 만들지 않는다.

자동 분석·일반 채팅은 build_graph/validate_answer를 공유한다.
두 질문 경로에서 payload의 1-based number와 실제 비연속 청크 ID가 정확히 연결되는 회귀 테스트를 추가했다.
근거/필드 검증을 완화하지 않았다. 근거 보호 규칙 위반도 별도 category로 유지한다.
기존 교정 재시도 한도는 유지하고 미분류 내부 오류는 반복해도 고쳐지지 않으므로 즉시 중단한다.

## Windows 재검증

```powershell
git pull --ff-only origin feat/required-rag-integration
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m streamlit run app.py
```

동일 삼성전자 사업보고서 자동 분석 및 일반 추가 질문을 각각 실행한다.
실패 시 새 exception/category/field/rule/attempt/evidence_count와 HTTP/finish 분류만 확인한다.
원문/키 출력 없이 규칙을 식별할 수 있다. 정상 응답은 기존 근거·공시별 기록/분석과 채팅 분리를 유지해야 한다.

검증 결과: Linux Python 3.12에서 전체 157개 통과 / 실패 0 / skip 0, 13.146초. 신규 7개는 규칙별 실패, 안전 로그, 분석/채팅 ID 매핑, 근거 보호와 내부 오류 재시도 중단을 검증한다. 외부 GMS는 Mock이며 실제 API 성공으로 간주하지 않는다.
