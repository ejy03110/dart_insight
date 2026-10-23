# F2014 MMR 검색 비교

구현 완료, **실제 색인 기반 Top 5 비교는 미실행**이다. F2014 전체 충족 판정은 Windows 로컬 실행 결과 확인 전까지 보류한다. 기존 RAG 서비스·Streamlit UI·운영 DB·색인·모델 캐시는 변경하지 않았다.

## 클라우드 실행 기록

다음 명령을 실제 실행했다.

```bash
.venv/bin/python scripts/compare_mmr.py --query '사업 위험과 대응 방안은 무엇인가요?' --output docs/f2014-mmr-comparison.md
```

종료 코드 `2`, 출력 `{"status": "blocked", "reason": "existing_index_missing"}`. 설정된 색인 경로와 `/workspace`, `/tmp`에 기존 `chroma.sqlite3`가 없고 기본 BGE 모델 캐시도 없다. 사용자 확인에 따르면 실제 색인·캐시는 Windows 로컬에만 있다. 이 차단 결과는 검색 비교 성공이 아니며 실제 순위·점수·다양성 개선 결과를 작성하지 않았다. 단위 테스트의 모의 벡터는 실제 공시 비교 자료로 사용하지 않았다.

## 최소 구현

- `dart_insight/rag/mmr.py`: cosine 기반 greedy MMR 및 동일 후보군 비교 함수. 최초는 질문 유사도가 가장 높은 후보, 이후 `lambda * cos(질문, 후보) - (1-lambda) * max cos(후보, 이미 선택한 결과)`를 최대화한다. 동점은 입력 순서 유지. `lambda=1`은 유사도 순, 기본 `0.5`는 관련성/중복 유사도의 절충이다.
- 기존 BGE-M3 어댑터로 질문을 **1회** 임베딩하고 기존 Chroma에서 최대 `fetch_k`개 후보와 저장 벡터를 **1회 query**로 가져온다. 동일 질문 벡터·후보·필터에서 유사도/MMR Top 5를 비교한다. 후보 밖 문서까지 비교한 전역 순위는 아니다.
- 기존 BGE 프로필·1024차원·cosine 컬렉션 확인. 재임베딩/추가 수집/색인/삭제/GMS 호출 없음. 생산 RAG의 메타 고정·공시 범위 검색이나 인용 검증에는 연결하지 않았다.
- 결과 문서는 두 방식의 순위/청크 ID/공시 제목/문서 접수번호/질문 cosine, 고유 문서 수, 평균 쌍별 cosine 및 청크 교집합을 기록한다. 쌍별 cosine 감소는 벡터 중복 감소 지표일 뿐 답변 정확성 향상을 보장하지 않는다.

## Windows 로컬 실행

프로젝트 루트에서 기존 가상환경을 사용한다. **Streamlit과 색인 작업을 먼저 종료**해 DB/벡터 파일이 변경되지 않는 상태에서 복사한다. CLI는 운영 DB를 직접 열지 않고 색인 디렉터리와 모델 캐시를 OS 임시 폴더에 복사한다. 임시 복사본만 자식 프로세스에서 사용하고 프로세스 종료 후 정리하므로 Windows Chroma/SQLite 핸들을 운영 파일에서 유지하지 않는다. 임시 디스크에 DB와 모델 캐시 복사본을 위한 여유 공간이 필요하다.

```powershell
.\.venv\Scripts\python.exe scripts\compare_mmr.py --db-dir ".local\data\chroma_bge_m3" --collection dart_documents --query "사업 위험과 대응 방안은 무엇인가요?" --fetch-k 20 --lambda-mult 0.5 --output "docs\f2014-mmr-comparison.md"
```

모델 캐시는 기존 `EMBEDDING_CACHE_DIR` 설정, 없으면 `.local/data/models`를 사용한다. 다른 위치에 실제 캐시가 있다면 **기존 캐시 경로**를 지정한다. `models--BAAI--bge-m3`가 있는 Hugging Face 캐시 상위 폴더를 지정할 수 있다. 키 입력이나 모델 재다운로드는 필요 없다.

```powershell
$env:EMBEDDING_CACHE_DIR = "C:\실제\기존\모델캐시"
```

CLI 자식은 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`로 기존 모델 파일만 읽는다. 원래 모델 캐시를 바꾸지 않도록 복사본을 어댑터에 전달한다. 캐시가 불완전하면 실패하며 새 모델을 다운로드하지 않는다.

- `dart_documents`는 본문 청크 비교다. 기존 메타데이터를 비교하려면 `--collection dart_metadata`를 명시한다. 서로 다른 컬렉션 결과를 같은 비교로 혼합하지 않는다.
- 기업 범위를 동일하게 제한하려면 `--corp-code <공식기업코드>`를 추가한다. 지정하지 않으면 전체 기업이다.
- 필터 후 후보가 5개 미만이면 실패하며 Top 5를 꾸미거나 재색인하지 않는다. 기존 색인 중 실제로 5개 이상인 비교 대상을 사용해야 한다.
- 성공(종료 `0`)하면 지정한 문서를 **실제 결과로 교체**한다. 차단/검색 실패/정리 실패 시 기존 비교 문서는 유지된다. 오류에는 정제된 상태·예외 유형만 출력한다.
- 로컬 결과를 전달받은 뒤 실제 순위 차이와 다양성/관련성 절충을 해석할 수 있다. 현재 문서는 로컬 실행 결과를 대신하지 않는다.

## 테스트

필요한 단위 테스트: MMR 순위/다양성 페널티, lambda 경계, 동점·후보 부족, 잘못된 벡터·파라미터, 동일 질문/후보군 1회 재사용 및 무쓰기, 색인 부재 시 DB/보고서 미생성. 단위 테스트 통과는 실제 색인 비교 통과를 뜻하지 않는다.

실행 결과: 신규 단위 **5개 통과**. 기존 포함 전체 **314 실행 / 313 통과 / 실패0 / 오류0 / Windows 전용 skip1**, 71.326초(Linux). 실제 Windows CLI/색인/BGE 비교는 미실행이다.
