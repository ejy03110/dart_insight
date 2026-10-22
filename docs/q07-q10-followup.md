# Q-07 / Q-10 후속 수정 및 로컬 확인

기준 커밋: `7138a35`. F2001~F2012 범위만 유지한다. 기존 .env·Chroma·SQLite·모델 캐시를 삭제하거나 초기화하지 않는다.

## Q-07: 확인된 구문 오류와 제한적인 문자 보존 복구

사용자가 실제 진단 ZIP에서 추출해 제공한 정보:

- 접수번호 `20250311001085`, 삼성전자 사업보고서 (2024.12), 제출일 2025-03-11.
- XML 3개, 첫 XML UTF-8 / 6,298,681바이트.
- 6263행: `<P>< TV 시장점유율 추이 ></P>`.

XML 시작 태그는 `<` 다음에 공백이 올 수 없다. 여기서는 제목을 나타내는 꺾쇠를 문자 엔티티로 표현하지 않아
엄격한 XML 파서가 6263행 4열에서 실패한다. 크기/인코딩 제한 문제가 아니라 **원본의 비정형 텍스트 표기**다.
제공받은 전후 행으로 동일 위치의 오류를 재현했다. 전체 실제 ZIP은 이 머신에 없으므로 다른 두 XML의 실제 내용은 미확인이다.

복구 범위를 다음과 같이 제한했다.

- P/SPAN 안의 **단독 제목형 텍스트**이며 꺾쇠 안쪽 양끝에 공백이 있고, 한글과 둘 이상의 단어가 있는 경우만 대상.
- 따옴표·속성 등호·슬래시·다른 태그·줄바꿈이 있는 경우는 대상에서 제외. 짧은 제목 범위로 제한한다.
- 해당 문자열의 꺾쇠만 `&lt;`, `&gt;`로 인코딩한 뒤 다시 엄격하게 XML을 파싱한다.
- 추출 결과는 원래 보였어야 할 `< TV 시장점유율 추이 >` 그대로다. 정상 태그·속성·CDATA·주석은 이 보정으로 재작성하지 않는다.
- 모든 XML을 검사하고 보정 건수·예시를 기록한다. 다른 XML에 알 수 없는 오류가 있으면 부분 본문을 반환/색인하지 않는다.
  손상된 태그를 임의로 닫거나 누락될 수 있는 본문을 묵인하는 일반 recover 모드는 사용하지 않는다.
- 100 MiB 압축/압축 해제 크기, XML 1,000개, 실제 읽기 크기 검사·엔티티 선언 거절·원격 DTD 비조회·경로 비추출 유지.

보정 안내는 CollectedDocument의 선택 필드 `parse_notes`로 저장한다. 이전 JSON/SQLite 문서는 그대로 읽을 수 있으며
SQLite 스키마를 바꾸거나 기존 완료 행을 삭제하지 않는다. 안내는 RAG 결과 audit와 분석/채팅의 **XML 보정 내역**에 유지된다.
이 문자 보정으로 삭제한 본문/생략한 XML은 없다고 표시한다. 미확인 손상을 건너뛰는 불완전 추출은 허용하지 않는다.

## 실제 Windows ZIP 검사 및 본문 추출

서버를 종료하고 로컬 변경을 보존한 뒤 브랜치를 갱신한다. 기존 가상환경/1,065건 DB를 그대로 사용한다.

```powershell
git status --short
git pull --ff-only origin feat/required-rag-integration
$env:PYTHONUTF8 = "1"
$zip = ".local/data/diagnostics/document-20250311001085-214461e002e44eea8cbc13d64670885c.zip"
.\.venv\Scripts\python.exe -m dart_insight.inspect_document --input $zip --show-repairs --text-output .local/data/diagnostics/20250311001085-verified.txt
```

사용자 지정 DART_DATA_DIR이면 경로를 바꾼다. 출력 파일이 이미 있으면 **새 파일명**을 사용한다. 덮어쓰지 않는다.
확인할 결과:

- `status=ok`, `xml_files_checked=3`, `xml_files_dropped=0`, 각 XML의 `text_characters`와 보정 내역.
- 생성한 실제 UTF-8 본문에서 TV 시장점유율 제목·앞뒤 문단·표 내용 확인.
- 실패하면 모든 `members`의 `error`, XML 순번·행/열을 확인한다. 새 본문 파일을 생성하거나 부분 추출을 RAG에 사용하지 않는다.
- `--show-repairs`는 복구한 제목의 짧은 예시를 의도적으로 출력한다. 기본 실행은 예시/전체 본문을 출력하지 않는다.
- CLI는 API 키·네트워크·BGE/GMS 없이 저장된 ZIP을 읽는다. 이 검사 통과가 실제 LLM 분석 품질까지 입증하지는 않는다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m streamlit run app.py
```

같은 사업보고서를 선택해 분석을 재시도하고 보정 안내·실제 근거·주변 수치가 유지되는지 확인한다.
기존 실패 원장은 재시도 가능 상태로 남아 있으므로 DB/원장을 삭제할 필요가 없다.
추가 오류가 나오면 진단 JSON 전체의 오류/보정 항목을 전달한다. .env나 키 값은 전달하지 않는다.

## Q-10: 개발자 단축키와 브라우저 복사

설치된 공식 Streamlit 1.62.0 설정 설명과 프런트엔드를 확인했다.
`client.toolbarMode="auto"`는 localhost에서 개발자 옵션을 노출한다. 개발자 `c` 단축키가 Clear caches 대화상자를 연다.
앱 자체에는 Ctrl+C 이벤트를 가로채는 코드가 없었다.

Linux Chromium 실제 관측:

| 실행 조건 | 선택 없음 Ctrl+C | 텍스트 선택 Ctrl+C | 일반 c |
|---|---|---|---|
| 기존 auto | 팝업 없음 | 정상 복사 / 팝업 없음 | Clear caches 팝업 재현 |
| viewer | 팝업 없음 | 정상 복사 / 팝업 없음 | 팝업 없음 |

**사용자가 보고한 Windows Ctrl+C 자체의 오작동은 이 머신에서 재현하지 못했다.**
수정은 확인된 Streamlit 캐시 삭제 단축키 진입 경로를 제거한다. Windows의 키 이벤트/IME/브라우저 차이를 추측해 확정하지 않는다.

프로젝트 `.streamlit/config.toml`에 공식 `client.toolbarMode="viewer"`를 설정했다.
메뉴를 CSS로 숨기거나 키보드 JavaScript를 가로채지 않는다. `st.cache_resource` 및 모델/DB 로딩 코드는 유지한다.
환경변수나 CLI로 toolbarMode를 별도 지정했다면 해당 override도 확인한다. 기본 프로젝트 설정이 실제로 적용됐는지 확인:

```powershell
.\.venv\Scripts\python.exe -c "import streamlit as st; print(st.get_option('client.toolbarMode'))"
```

`viewer`가 출력되어야 한다. 설정 변경 후 서버 재시작과 브라우저 새로고침을 수행한다.

분석과 채팅 답변에 **전체 답변 복사** 펼침 영역을 추가했다. Streamlit `st.code`의 기본 복사 아이콘을 사용한다.
복사 영역에 포인터를 올리거나 Tab으로 포커스한 뒤 오른쪽 위 아이콘을 누른다. 답변 전체의 Markdown 텍스트가 복사된다.
커스텀 clipboard 스크립트나 임의 HTML은 없다.

공식 참고: [config.toml](https://docs.streamlit.io/develop/api-reference/configuration/config.toml),
[st.code](https://docs.streamlit.io/develop/api-reference/text/st.code).
이번 환경에서 웹 문서는 접근 403이었고, 설치된 공식 버전의 설정 설명/실행 동작을 기준으로 검증했다.

Windows Chrome/Edge 재검증:

1. 분석/채팅 텍스트를 드래그해 Ctrl+C → 메모장에 붙여넣기. 팝업 없이 선택 내용과 일치하는지 확인.
2. 선택을 해제한 상태에서 Ctrl+C 및 일반 c → 캐시 삭제 팝업이 없는지 확인.
3. 분석과 채팅 각각의 복사 아이콘 → 메모장에 붙여넣어 줄바꿈·한글·근거 번호 확인.
4. 후속 질문/공시 전환으로 캐시를 재사용할 때 모델이 불필요하게 다시 로딩되지 않는지 확인.
5. 계속 팝업이 뜨면 toolbarMode 출력, 브라우저/버전, 텍스트 선택 유무, IME 상태, 실제 누른 키를 기록.
   개발자 도구에서 임의 코드를 실행하거나 캐시 삭제를 누를 필요는 없다.

## 검증 증거와 남은 항목

전체 unittest **142개 통과, 실패 0, skip 0** / 15.443초 / Linux Python 3.12.14. 기존 135개 + 신규 7개.
pip check·compileall·git diff --check 통과. Windows/Python 3.11 직접 실행은 미수행이다.

- 제공된 실제 구문을 재현하는 회귀 입력, 정상 태그/CDATA/수치 보존, 3개 XML 검사/두 번째 오류 거절, 새 UTF-8 출력/덮어쓰기 방지.
- 실제 임시 Chroma/SQLite/LangGraph로 복구 텍스트→색인→근거→답변 및 재질문의 안내 유지 검증.
  HTTP/LLM/BGE는 Mock이며 실제 데이터를 대체한 운영 경로는 없다.
- Linux Chromium 154에서 네이티브 선택 복사, 분석/채팅 두 복사 아이콘의 **실제 클립보드 내용 일치**, 재실행 후 resource cache 유지 확인.
  브라우저 테스트는 명시된 테스트 전용 텍스트와 production 복사 UI를 공유하는 fixture다.
- Windows 직접 검증·실제 ZIP 세 파일 전체·실제 OpenDART/BGE/GMS E2E는 미검증. 클라우드에 ZIP·API 키·1,065건 DB가 없다.

선택적인 브라우저 자동 검증 파일: `tests/browser_clipboard_fixture.py`, `tests/check_clipboard_browser.py`.
Playwright는 별도 개발 환경에서만 설치하며 운영 requirements/lockfile에 추가하지 않았다.
