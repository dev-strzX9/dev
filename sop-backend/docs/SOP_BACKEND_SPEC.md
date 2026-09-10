# SOP Studio 백엔드 개발 스펙

> 이 문서는 개발자(또는 Claude Code)에게 그대로 넘겨 백엔드를 구현하기 위한 명세입니다.
> 같이 전달할 파일: `sop_schema.sql`(DB 스키마), `SOP_EXPORT_3.html`(현재 프론트엔드 단일 파일).

---

## 0. 한 줄 요약

브라우저 단일 HTML 편집기(SOP Studio)가 만드는 **문서 JSON을 PostgreSQL에 버전 단위로 저장·조회**하는 REST API를 FastAPI로 만든다.
프론트는 이미 완성되어 있고, 저장소 어댑터(`libStore`)의 함수 4개만 API 호출로 바꾸면 연결된다.

---

## 1. 배경과 현재 상태

- 프론트: `SOP_EXPORT_3.html` 하나. 순수 HTML/CSS/JS, 프레임워크 없음. 순서도 편집기는 base64로 내장된 iframe.
- 문서 = 16:9 페이지들의 나열. 페이지 종류: 표지(title) / 순서도(flowchart) / Activity 정의서(activity, activity-auto) / 적용범위(scope) / 본문(body-template).
- 현재 저장 방식: 브라우저 IndexedDB(라이브러리 사이드바) + localStorage(자동 임시 저장) + JSON 파일 다운로드("원본 저장").
- 목표: 위 저장을 서버로 옮긴다. 편집기 UI·기능은 바꾸지 않는다.

---

## 2. 기술 스택 (고정)

| 항목 | 선택 |
|---|---|
| 언어 / 프레임워크 | Python 3.11+, FastAPI |
| DB 접근 | `psycopg` v3 (async) + `psycopg_pool.AsyncConnectionPool`. ORM 사용하지 않음 |
| DB | PostgreSQL 15+, 확장 `pg_trgm` 필수 (`vector`, `pg_search`는 이번 범위 밖) |
| 검증 | pydantic v2 |
| 마이그레이션 | 이번 단계는 `sop_schema.sql` 직접 실행. (alembic 등 도구 도입은 보류) |
| 설정 | 환경변수 (`DATABASE_URL`, `CORS_ORIGINS`, `STATIC_DIR`) |
| 테스트 | pytest + httpx AsyncClient, 테스트용 DB는 환경변수로 분리 |

---

## 3. 데이터 모델

스키마 전체는 `sop_schema.sql` 참고. 핵심만:

```
sop_documents (SOP 1건, id 고정)
   └─ sop_versions (저장 1회 = 1행, content jsonb = 편집기 JSON 통째)
         ├─ flow_nodes  (content에서 풀어낸 순서도 노드, 검색용 사본)
         └─ flow_edges  (연결선)
sop_edit_locks (동시 편집 방지)   sop_drafts (자동 임시 저장)
```

### 원칙 (반드시 지킬 것)
1. **편집기가 읽고 쓰는 건 `sop_versions.content` 하나뿐.** `flow_nodes/edges`는 저장 시 서버가 파생해서 채우는 읽기용 사본이며, 복원에는 절대 쓰지 않는다.
2. **버전 행은 불변.** 수정은 항상 새 버전 INSERT. `flow_nodes/edges`도 새 버전 것만 INSERT하고 이전 버전 행은 건드리지 않는다.
3. **"현재 것" 조회는 항상 `sop_documents.current_version_id`로 조인.** `flow_nodes`를 그냥 조회하면 모든 버전이 섞인다.
4. `sop_documents.id`(uuid)는 문서의 영구 식별자. SOP 번호(`sop_no`)는 바뀔 수 있는 표시값.

### 편집기 JSON 형식 (입력 계약)
```jsonc
{
  "format": "sop-editor-mock",      // 반드시 이 값
  "version": 1,                     // format_version
  "savedAt": "2026-09-09T…",
  "sop":    { "id": "SOP-ETCH-001", "name": "Chamber PM 절차", "desc": "…" },
  "studio": { "owner": "홍길동/장비기술", "revision": "1.0", "area": "P", "tags": ["PM","Chamber"] },
  "mode":   "edit",
  "blocks": [
    { "type": "title", "main": "<h2 HTML>", "meta": "…" },
    { "type": "flowchart", "instanceId": "flow_17257…_3",
      "project": { "sopInfo": {…}, "nodes": [ {…} ], "edges": [ {…} ], "counters": {…}, "ui": {…} } },
    { "type": "activity", "title": "…", "rows": 5, "cols": 4, "cells": ["…"] },
    { "type": "activity-auto", "sourceInstanceId": "flow_…", "title": "…", "headers": [], "rows": [], "colWidths": [], "rowHeights": {}, "fontSize": 13 },
    { "type": "scope", "cells": ["제목","분류","목적","적용범위"] },
    { "type": "body-template", "title": "…", "objects": [ { "kind": "text"|"image", "left":"8%", "top":"8%", "width":"44%", "height":"26%", "zIndex":"10", "html":"…", "src":"data:image/…;base64,…", "alt":"…" } ] }
  ]
}
```
순서도 노드(`project.nodes[]`) 필드: `node`(키, "seq_3"), `node_type`(start|seq|decision|sop|end), `name`, `role_owner`, `action`, `description`, `systems`([{name, menus[]}]), `manual`([]), `sop_id`, `sop_name`, `x`, `y`, `font_size`.
연결선(`project.edges[]`): `edge`, `source`, `target`, `sourcePort`, `targetPort`, `condition`, `line_type`, `route`.

- 서버는 이 JSON을 **해석하지 않고 통째로 보관**하되, 위 필드만 골라 `flow_nodes/edges`와 문서 메타에 복사한다.
- 모르는 필드는 버리지 말고 `content`에 그대로 남긴다 (프론트가 계속 진화하므로).
- `format`이 다르거나 `blocks`가 배열이 아니면 422.

### 컬럼 대응
| JSON | 컬럼 |
|---|---|
| `sop.id` | `sop_documents.sop_no` |
| `sop.name` | `sop_documents.name` |
| `studio.area` | `sop_documents.area` (없거나 P/E/D/T/C 외 값이면 `''`) |
| `studio.revision` / `owner` / `tags` | `sop_versions.revision` / `owner` / `tags` |
| `format` / `version` | `sop_versions.format` / `format_version` |
| 전체 | `sop_versions.content` |
| `blocks[flowchart].instanceId` + `project.nodes[]` | `flow_nodes` (한 노드 = 한 행) |
| `blocks[flowchart].instanceId` + `project.edges[]` | `flow_edges` |

---

## 4. API

Base path `/api`. JSON in/out. 시간은 ISO 8601 UTC. 오류는 `{ "error": { "code": "…", "message": "…" } }`.

### 4.1 문서 목록 — 라이브러리 트리용
`GET /api/sops?status=!retired&q=&area=`

- `content`는 절대 포함하지 않는다 (문서당 수백 KB).
- 응답 예:
```json
[{ "id": "3f2a…", "sop_no": "SOP-ETCH-001", "name": "Chamber PM 절차", "area": "P",
   "status": "draft", "version_no": 3, "revision": "1.2", "owner": "홍길동",
   "updated_at": "2026-09-09T02:11:00Z" }]
```
- 정렬: `area, sop_no` (자연 정렬은 프론트가 함). `q`는 `sop_no ILIKE` 또는 `name ILIKE` (pg_trgm 인덱스).

### 4.2 문서 열기 — 최신 버전
`GET /api/sops/{doc_id}` → `{ "id", "sop_no", "version_no", "version_id", "lock": {…}|null, "content": {…} }`

`content`는 저장했던 JSON 그대로. 프론트는 이걸 `restoreDocumentState()`에 넘긴다.

### 4.3 특정 버전 열기
`GET /api/sops/{doc_id}/versions/{version_no}` → 같은 형태.
`GET /api/sops/{doc_id}/versions` → 버전 목록 (`version_no, revision, saved_by, saved_at, change_note`), `content` 제외.

### 4.4 저장 — 새 버전 생성
`PUT /api/sops/{sop_no}`
```json
{ "doc": { …편집기 JSON… }, "base_version_no": 3, "change_note": "Step 4 추가", "saved_by": "hong" }
```
- `sop_no` 경로값과 `doc.sop.id`가 다르면 400.
- 문서가 없으면 새로 만든다 (upsert). 있으면 `name/area/updated_at` 갱신.
- **하나의 트랜잭션**으로:
  1. `sop_documents` upsert (`ON CONFLICT (sop_no)`)
  2. `SELECT COALESCE(MAX(version_no),0) FROM sop_versions WHERE document_id=$1 FOR UPDATE` — 행 잠금
  3. `base_version_no`가 주어졌고 현재 최대값과 다르면 **409** `{code:"version_conflict", current_version_no}` (다른 사람이 먼저 저장함). `base_version_no`가 null이면 검사 생략(신규/강제).
  4. `sop_versions` INSERT (`version_no = max+1`, `content = doc`)
  5. `flow_nodes`, `flow_edges` INSERT (`executemany`)
  6. `sop_documents.current_version_id = 새 id`
  7. 있으면 `sop_drafts` 의 이 문서·이 사용자 행 삭제
- 응답 201: `{ "id", "sop_no", "version_id", "version_no": 4, "saved_at" }`
- `content` 크기 제한 20MB (이미지 base64 때문에 큼). 초과 시 413.

### 4.5 삭제 = 폐기
`DELETE /api/sops/{doc_id}` → `status='retired'`로 변경 (행 삭제 아님). 목록에서 기본 제외.
`POST /api/sops/{doc_id}/restore` → `status='draft'`로 복구.
(진짜 삭제는 이번 범위 밖. SOP는 이력 보존이 원칙.)

### 4.6 편집 잠금
- `POST /api/sops/{doc_id}/lock` body `{ "user": "hong", "ttl_sec": 120 }`
  - 잠금 없음 / 내 잠금 / 만료된 잠금 → 획득 200 `{ locked_by, expires_at }`
  - 남의 유효 잠금 → 423 `{ code:"locked", locked_by, expires_at }`
  - 같은 호출이 하트비트 겸용 (`expires_at` 연장)
- `DELETE /api/sops/{doc_id}/lock` body `{ "user" }` → 내 잠금만 해제
- 4.4 저장은 잠금 여부와 무관하게 동작하되(버전 충돌 검사가 안전망), 응답에 다른 사람 잠금이 있으면 `warning` 필드로 알려준다.

### 4.7 자동 임시 저장 (선택, 2차)
- `PUT /api/sops/{doc_id}/draft` body `{ "user", "content" }` — 덮어쓰기, 버전 안 쌓임
- `GET /api/sops/{doc_id}/draft?user=` / `DELETE …`

### 4.8 부가
- `GET /api/health` → DB 연결 확인 `{ "ok": true, "db": "up" }`
- `GET /api/sops/{doc_id}/versions/{a}/diff/{b}` (선택) → 두 버전의 `flow_nodes` 차이 (추가/삭제/수정 노드 목록)

### 인증
이번 단계에서는 없음. `saved_by`/`user`는 클라이언트가 보낸 값을 그대로 쓴다. 단, 나중에 붙일 수 있게 **모든 핸들러가 `current_user` 의존성 하나를 통해 사용자 이름을 받도록** 구조를 잡는다 (지금은 헤더 `X-User` 또는 기본값 `"anonymous"`).

---

## 5. 프론트 연동

### 5.1 정적 파일
FastAPI가 `STATIC_DIR`의 `SOP_EXPORT_3.html`을 `/`에서 서빙한다 (같은 오리진이면 CORS 불필요). 별도 호스팅 시 `CORS_ORIGINS`로 허용.

### 5.2 프론트 수정 범위 (최소)
`SOP_EXPORT_3.html` 안의 `libStore` 객체 네 함수만 교체한다. 나머지 UI(트리, 검색, 열기, 삭제)는 이 함수들의 결과 형식만 맞으면 그대로 동작한다.

```js
const libStore = {
  async list()      { return (await api('GET', '/api/sops')); },                 // [{sop_no, name, area, revision, owner, updated_at, id, version_no}]
  async get(no)     { const d = await api('GET', `/api/sops/by-no/${encodeURIComponent(no)}`); return { sop_no: no, doc: d.content, version_no: d.version_no, id: d.id }; },
  async put(rec)    { return api('PUT', `/api/sops/${encodeURIComponent(rec.sop_no)}`, { doc: rec.doc, base_version_no: rec.base_version_no ?? null, saved_by: currentUser }); },
  async remove(no)  { const d = await libStore.get(no); return api('DELETE', `/api/sops/${d.id}`); }
};
```
- 그래서 **`GET /api/sops/by-no/{sop_no}`** 도 제공한다 (트리는 sop_no로 항목을 식별함).
- 프론트가 현재 열린 문서의 `version_no`를 기억했다가 저장 시 `base_version_no`로 보내도록 `libOpenDocument`/`libSaveCurrent`에 변수 하나 추가.
- 409를 받으면 "다른 사람이 v4를 저장했습니다. 다시 불러온 뒤 저장하세요" 안내.
- "JSON 가져오기 / 전체 내보내기"는 IndexedDB 시절 데이터 이관용으로 남겨둔다 (가져오기는 `PUT` 반복 호출로).

### 5.3 기존 브라우저 데이터 이관
프론트의 "전체 내보내기"가 만드는 `{format:"sop-studio-library", items:[{sop_no, doc,…}]}` 파일을 받아 일괄 저장하는 CLI 스크립트 하나: `python -m app.tools.import_library sop-library-2026-09-09.json`.

---

## 6. 검증 규칙 (서버)

- `doc.format == "sop-editor-mock"`, `doc.version` 정수, `doc.blocks` 배열 — 아니면 422.
- `doc.sop.id` 공백 불가. 허용 문자: 영문·숫자·`-`·`_`·`.` (한글 번호 필요하면 알려줄 것).
- `studio.area` ∈ {"", P, E, D, T, C}. 그 외 값은 `''`로 저장하고 응답 `warnings`에 기록.
- 노드 파싱 실패(필드 형식 이상)는 **저장을 막지 않는다.** 해당 노드만 건너뛰고 `warnings`에 남긴다. 원본 `content`는 항상 보존되므로 프론트가 복원에는 문제 없다.
- HTML 필드(`title.main`, `cells[]`, `objects[].html`)는 그대로 저장. 서버에서 렌더링하지 않으므로 XSS 정화는 프론트 몫(이미 `sanitizeHtml` 있음). 단 응답 헤더 `Content-Type: application/json` 고정.

---

## 7. 구현 세부 지침

- 커넥션 풀: 앱 시작 시 `AsyncConnectionPool(min_size=2, max_size=10, open=False)` → `lifespan`에서 open/close.
- 트랜잭션 안에서는 DB 작업만. 외부 호출·긴 계산 금지 (FOR UPDATE 잠금이 늘어짐).
- SQL은 파라미터 바인딩만 사용. 문자열 포매팅 금지.
- `content`는 `psycopg.types.json.Jsonb`로 넘긴다. 읽을 때는 dict로 자동 변환됨.
- `flow_nodes` 파생 로직은 순수 함수 `derive_flow_rows(doc) -> (node_rows, edge_rows)` 로 분리하고 단위 테스트를 붙인다. 프론트 JSON 형식이 바뀌면 이 함수만 손보면 되게.
- 로깅: 요청 1줄(메서드, 경로, 상태, 소요 ms) + 저장 시 `sop_no, version_no, 노드 수`.
- 에러: 예상 가능한 것은 위 코드(400/404/409/413/422/423)로, 나머지는 500 + 로그. 스택트레이스를 응답에 넣지 않는다.

### 디렉터리
```
sop-backend/
  app/
    main.py            # FastAPI 앱, lifespan, 정적 파일
    config.py          # 환경변수
    db.py              # 풀, 트랜잭션 헬퍼
    schemas.py         # pydantic 모델 (요청/응답)
    derive.py          # derive_flow_rows()
    routers/
      sops.py          # 목록/열기/저장/폐기
      versions.py      # 버전 목록/특정 버전/diff
      locks.py         # 잠금
      drafts.py        # 임시 저장 (2차)
    tools/import_library.py
  sql/sop_schema.sql
  tests/
    test_derive.py     # JSON → 행 변환
    test_save_flow.py  # 저장 → 버전 증가 → 노드 행 수 → 열기 시 content 동일
    test_conflict.py   # base_version_no 불일치 409
    test_locks.py      # 획득/충돌/만료/하트비트
  static/SOP_EXPORT_3.html
  README.md            # 실행법, 환경변수, curl 예시
  pyproject.toml
```

---

## 8. 완료 기준 (Definition of Done)

1. `docker compose up`(postgres + api) 또는 로컬 PG 연결로 `/api/health`가 200.
2. 브라우저에서 HTML을 열고: 문서 작성 → "현재 문서를 라이브러리에 저장" → 새로고침 → 트리에 보임 → 클릭해서 열림 → 순서도·본문·이미지 그대로 복원.
3. 같은 문서를 두 번 저장하면 `version_no`가 1 → 2, `flow_nodes`에 두 버전의 행이 모두 있음.
4. 두 탭에서 같은 문서를 열고 한쪽이 저장한 뒤 다른 쪽이 저장하면 409.
5. 폐기한 문서는 목록에서 사라지고 `restore`로 돌아옴.
6. `pytest` 전부 통과. README의 curl 예시가 실제로 동작.

---

## 9. 이번 범위에 넣지 않는 것 (나중에)

- 사용자 인증·권한 (SSO). 지금은 `X-User` 헤더.
- SOP 참조 노드를 문서 id로 연결하는 것(`ref_document_id`), 참조 대상 개정 시 검토 알림.
- 검색: pgvector 임베딩, pg_search BM25, `sop_chunks`. (스키마에 자리만 예약)
- 이미지 base64를 오브젝트 스토리지로 분리. 지금은 `content` 안에 그대로.
- 승인 워크플로(draft → review → approved). `status` 컬럼만 두고 전이 규칙은 없음.
- PPT/PDF 서버 생성. 지금은 브라우저에서 pptxgenjs로 생성.

---

## 10. 우선순위

1. 스키마 적용 + 4.1 / 4.2 / 4.4 (목록·열기·저장) + 정적 서빙 + `libStore` 교체 → **이걸로 end-to-end 동작**
2. 4.5 폐기/복구, 4.3 버전 목록, 이관 스크립트
3. 4.6 잠금 + 프론트 하트비트(30초) + 잠금 표시
4. 4.7 임시 저장, 4.8 diff

각 단계가 끝날 때마다 위 완료 기준으로 확인한다.
