# SOP Studio backend

첨부된 SOP 편집기를 FastAPI + PostgreSQL에 연결합니다. 문서 JSON 원본은 `sop_versions.content`에 그대로 저장하고, 저장할 때마다 불변 버전을 추가합니다. 검색용 순서도 노드/연결선은 버전별 사본입니다.

## 빠른 실행

```bash
cd sop-backend
docker compose up --build
```

- 편집기: http://localhost:8000
- API 문서: http://localhost:8000/docs
- 상태: http://localhost:8000/api/health
- PostgreSQL 16, `pg_trgm`, 초기 스키마가 자동 준비됩니다. DB는 named volume에 유지됩니다.
- 스키마 초기화는 **빈 DB를 처음 생성할 때 한 번만** 실행됩니다. 기존 DB에는 `psql`로 스키마를 직접 적용하세요. 데이터를 유지해야 하면 volume을 삭제하지 마세요.
- 개발용 DB 계정은 `sop/sop`입니다. 로컬 포트는 loopback에만 열립니다. `POSTGRES_PASSWORD`로 다른 비밀번호를 설정할 수 있습니다.

## 로컬 Python 실행

Python 3.11+, PostgreSQL 15+와 `pg_trgm` 확장 설치 권한이 필요합니다. 아래 예시는 비어 있는 `sop` DB와 `sop` 계정이 준비된 경우입니다.

```bash
cd sop-backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e '.[test]'
export DATABASE_URL='postgresql://sop:sop@localhost:5432/sop'
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/sop_schema.sql
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

| 환경변수 | 기본값 / 의미 |
|---|---|
| `DATABASE_URL` | `postgresql://sop:sop@localhost:5432/sop` |
| `CORS_ORIGINS` | 빈 값. 별도 프론트 오리진은 쉼표 구분 또는 JSON 배열로 지정 |
| `STATIC_DIR` | 프로젝트의 `static/` 절대 경로. 사용자 지정 상대 경로는 실행 디렉터리 기준 |
| `TEST_DATABASE_URL` | 필수 테스트 DB URL. 운영/개발 DB와 분리 |

`.env.example`은 참고 파일이며 Python 앱이 `.env`를 자동으로 읽지는 않습니다. 셸 또는 실행 환경에서 환경변수를 지정하세요. Docker Compose는 프로젝트 `.env`의 Compose 변수를 읽습니다.

## 편집기 사용

1. `http://localhost:8000`을 열고 왼쪽 **사용자**에 사용자 이름을 입력합니다. 기본값은 `anonymous`입니다.
2. SOP 번호(영문·숫자·`-_.`), 이름, AREA를 작성합니다. 표지·순서도·본문·이미지 편집은 첨부 HTML의 기존 기능을 사용합니다.
3. **서버 저장**을 누르면 v1이 생성되고 왼쪽 AREA별 라이브러리에 나타납니다. 다시 저장하면 v2, v3가 됩니다.
4. 새로고침 후 라이브러리 문서를 클릭하면 서버 JSON으로 복원됩니다. 브라우저 임시 문서가 있으면 복구 여부를 선택할 수 있습니다.
5. 같은 문서를 두 탭에서 열고 첫 탭에서 저장한 뒤 두 번째 탭에서 저장하면 충돌 안내가 나옵니다. 원본 JSON으로 작업을 보관한 뒤 최신 문서를 다시 열어 반영하세요. UI에는 강제 덮어쓰기 기능이 없습니다.
6. 각 문서의 **이력**에서 이전 버전 원본을 다운로드합니다. **폐기**는 버전을 삭제하지 않으며 상태 필터를 `폐기됨`으로 바꾸어 **복구**할 수 있습니다.

**원본 저장 / Ctrl·Cmd+S / PPT 내보내기 / 적재 JSON 다운로드**는 기존 동작을 유지합니다. 서버 버전 생성은 별도의 **서버 저장** 버튼으로 수행합니다.

- 라이브러리 문서를 열거나 저장하면 120초 편집 잠금을 요청하고 30초마다 갱신합니다. 다른 사용자의 잠금은 표시하고 저장 응답에서 경고합니다. 잠금은 advisory이며 실제 저장 충돌 방지는 `base_version_no`가 담당합니다.
- 같은 사용자 이름은 같은 잠금 소유자로 간주됩니다. 사용자 이름은 인증된 신원이 아닙니다.
- 기존 브라우저 임시 저장에 더해, 서버에서 식별된 문서는 사용자별 서버 임시 저장도 수행합니다. 아직 한 번도 저장하지 않은 새 문서는 브라우저에만 임시 저장됩니다.
- 임시 문서는 원래의 기준 버전을 유지합니다. 오래된 임시 문서를 복구한 뒤 최신 서버 버전을 조용히 덮어쓰지 않습니다.
- 라이브러리 전체 내보내기는 폐기된 문서를 포함한 **각 문서의 최신 원본**을 내보냅니다. 모든 버전의 백업은 PostgreSQL 백업을 사용하세요.

## API 예시

```bash
curl -f http://localhost:8000/api/health

curl -f -X PUT http://localhost:8000/api/sops/SOP-DEMO-001 \
  -H 'Content-Type: application/json' -H 'X-User: hong' \
  -d '{"doc":{"format":"sop-editor-mock","version":1,"sop":{"id":"SOP-DEMO-001","name":"PM 절차"},"studio":{"area":"P"},"blocks":[]},"base_version_no":0,"change_note":"최초 저장"}'

curl -f 'http://localhost:8000/api/sops?q=PM&area=P'
curl -f http://localhost:8000/api/sops/by-no/SOP-DEMO-001
```

아래 `{doc_id}`는 저장 응답의 UUID로 바꿉니다.

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/api/sops?status=!retired&q=&area=` | 최신 메타 목록, 원본 제외. `status=all`로 모든 상태 조회 |
| GET | `/api/sops/by-no/{sop_no}` | SOP 번호로 최신 원본 |
| GET | `/api/sops/{doc_id}` | UUID로 최신 원본과 유효 잠금 |
| PUT | `/api/sops/{sop_no}` | 새 버전 생성. `doc`, `base_version_no`, `saved_by`, `change_note` |
| DELETE / POST | `/api/sops/{doc_id}` / `.../restore` | 폐기 / 복구 |
| GET | `/api/sops/{doc_id}/versions` | 버전 목록, 원본 제외 |
| GET | `/api/sops/{doc_id}/versions/{version_no}` | 특정 버전 원본 |
| GET | `/api/sops/{doc_id}/versions/{a}/diff/{b}` | 노드 추가·삭제·수정 비교 |
| POST / DELETE | `/api/sops/{doc_id}/lock` | 잠금 획득·갱신 / 해제. body `{"user":"hong","ttl_sec":120}` / `{"user":"hong"}` |
| PUT | `/api/sops/{doc_id}/draft` | body `{"user":"hong","content":{...}}` |
| GET / DELETE | `/api/sops/{doc_id}/draft?user=hong` | 사용자별 임시 문서 조회 / 삭제 |

- `base_version_no=0`: 아직 없는 문서만 생성. 기존 번호는 409.
- `base_version_no=N`: 현재 버전 N과 같을 때만 N+1 생성.
- `base_version_no=null` 또는 생략: 명세에 따른 명시적 검사 생략. API 호출자 책임이며 이전 버전을 삭제하지 않고 새 버전을 추가합니다. 편집기/기본 이관은 이 옵션을 사용하지 않습니다.
- `saved_by`/body `user`가 있으면 우선하고, 없으면 공통 `current_user` 의존성이 `X-User` 또는 `anonymous`를 제공합니다. 추후 SSO 도입 시 의존성과 body 사용자 override 정책을 함께 교체하세요.
- 오류 형식은 `{"error":{"code":"...","message":"..."}}`입니다. 충돌 응답 `error.current_version_no`, 잠금 충돌 `error.locked_by/expires_at`을 제공합니다.
- 원본 compact UTF-8 JSON은 최대 20 MiB, 전송 요청 전체는 최대 21 MiB입니다. 지나치게 공백이 많은 JSON이나 escape로 팽창한 요청은 원본 크기보다 먼저 전송 제한에 도달할 수 있습니다. chunked 요청도 제한합니다.
- 알 수 없는 JSON 필드는 원본에 보존합니다. 잘못된 AREA는 메타만 빈 값으로 저장하며, 잘못된 파생 노드/연결선은 건너뛰고 `warnings`를 반환합니다.

## 브라우저 데이터 이관

```bash
python -m app.tools.import_library sop-library-2026-09-09.json --user hong
# 이미 있는 SOP에도 새 버전을 추가하려는 경우에만:
python -m app.tools.import_library sop-library-2026-09-09.json --user hong --force
```

`--api-url` 기본값은 `http://localhost:8000`입니다. 기본 이관은 기존 SOP에 409를 반환하므로 파일을 재실행해도 중복 버전이 생기지 않습니다. 항목별 성공/실패를 출력하고 실패가 있으면 종료 코드 1을 반환합니다. 묶음 전체를 하나의 트랜잭션으로 저장하지 않습니다. 폐기 상태와 과거 버전 이력은 이관 대상이 아니며 JSON 원본을 새 문서로 저장합니다.

## 테스트

```bash
python -m pip install -e '.[test]'
# 별도 빈 테스트 DB 생성 (Compose 사용 시):
docker compose exec db createdb -U sop sop_test
export TEST_DATABASE_URL='postgresql://sop:sop@localhost:5432/sop_test'
python -m pytest -q
```

테스트는 각 케이스마다 임의 스키마를 만들어 격리하고 종료 시 해당 스키마만 삭제합니다. `TEST_DATABASE_URL`이 없으면 DB 통합 테스트는 skip됩니다. `pg_trgm`을 준비할 권한이 필요합니다. GitHub Actions는 PostgreSQL 16 서비스로 전체 테스트를 실행합니다.

검증 범위: JSON 원본 보존(HTML·이미지·미지 필드), 파생 행/불변 버전, 신규·기존 문서 동시 저장, 충돌 시 메타와 행 rollback, 폐기·복구, 잠금 경쟁·만료·heartbeat, 사용자별 임시 저장, diff, 입력/크기 제한, 정적 파일 서빙.

## 첨부 명세와의 조정

- 첨부 HTML은 `SOP_STUDIO_FINAL.html`이고 명세의 `libStore` 및 라이브러리 UI가 없었습니다. 원본 편집기와 iframe을 유지하고 `library.js`/`library.css` 및 작은 bridge를 추가했습니다. `/`는 이 파일을 우선 서빙하며 `SOP_EXPORT_3.html`도 대체 파일명으로 지원합니다.
- 명세의 `MAX(version_no) ... FOR UPDATE`는 PostgreSQL에서 집계 쿼리에 사용할 수 없습니다. 문서 upsert 후 **부모 문서 행을 FOR UPDATE로 잠근 뒤** 버전 최대값을 읽습니다. 모든 저장은 같은 문서 행에서 직렬화됩니다.
- `sop_no ILIKE` 검색을 위해 누락된 SOP 번호 trigram 인덱스를 추가했습니다.
- 번호 변경 전용 API는 범위에 없습니다. 현재 편집 화면의 번호를 바꾸어 저장하면 새 문서로 저장합니다. 영구 UUID를 유지하는 번호 변경이 필요하면 별도 rename API가 필요합니다.
- 이번 단계는 인증·승인 워크플로·임베딩·서버 PPT/PDF 생성을 포함하지 않습니다. 기존 브라우저 PPT 기능은 외부 CDN 의존성을 그대로 사용합니다.
