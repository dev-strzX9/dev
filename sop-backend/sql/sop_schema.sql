-- =====================================================================
--  SOP Studio — PostgreSQL 스키마 (v1)
--  PostgreSQL 15+ 권장.  gen_random_uuid()는 13+ 기본 내장.
--
--  구조 한눈에:
--    sop_documents 1 ─── N sop_versions 1 ─── N flow_nodes / flow_edges
--         │                     └── content jsonb (편집기 JSON 원본, 복원용)
--         ├── current_version_id  → "지금 최신" 포인터
--         ├── sop_edit_locks (동시 편집 방지, 선택)
--         └── sop_drafts     (자동 임시 저장, 버전 안 쌓임, 선택)
--
--  원칙
--   1. 편집기 저장/복원은 sop_versions.content 하나로 끝난다.
--   2. flow_nodes / flow_edges 는 content에서 파생한 "검색용 사본". 편집기는 읽지 않는다.
--   3. 버전 행은 불변. 저장할 때마다 새 행 + 새 노드 행.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- 한글 부분 검색 (필수)
-- CREATE EXTENSION IF NOT EXISTS vector;    -- 임베딩 검색 (나중에)
-- CREATE EXTENSION IF NOT EXISTS pg_search; -- BM25 한국어 검색 (있으면)

-- ---------------------------------------------------------------------
-- 1. 문서 (SOP 한 건 = 한 행). 개정해도, 번호가 바뀌어도 id는 고정.
-- ---------------------------------------------------------------------
CREATE TABLE sop_documents (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sop_no             text NOT NULL UNIQUE,              -- "SOP-ETCH-001"  (라이브러리 트리 정렬 키)
    name               text NOT NULL DEFAULT '',          -- SOP 이름
    area               text NOT NULL DEFAULT ''           -- 적용 AREA: P / E / D / T / C / ''(미지정)
                       CHECK (area IN ('', 'P', 'E', 'D', 'T', 'C')),
    status             text NOT NULL DEFAULT 'draft'      -- draft / review / approved / retired
                       CHECK (status IN ('draft', 'review', 'approved', 'retired')),
    current_version_id uuid,                              -- FK는 아래에서 추가 (순환 참조)
    created_by         text NOT NULL DEFAULT '',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2. 버전 (저장 1회 = 1행). content 에 편집기 JSON 통째.
-- ---------------------------------------------------------------------
CREATE TABLE sop_versions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    uuid NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
    version_no     int  NOT NULL,                         -- 1, 2, 3 ... 사람이 읽는 순번
    format         text NOT NULL,                         -- 편집기 JSON의 format  ("sop-editor-mock")
    format_version int  NOT NULL,                         -- 편집기 JSON의 version (마이그레이션용)
    content        jsonb NOT NULL,                        -- 편집기 "원본 저장" JSON 그대로
    revision       text NOT NULL DEFAULT '',              -- 편집기 "개정 번호" 입력값 (예: 1.0)
    owner          text NOT NULL DEFAULT '',              -- 작성자 / 부서
    tags           text[] NOT NULL DEFAULT '{}',          -- 검색 태그
    change_note    text NOT NULL DEFAULT '',
    saved_by       text NOT NULL DEFAULT '',
    saved_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_no)
);

ALTER TABLE sop_documents
    ADD CONSTRAINT fk_documents_current_version
    FOREIGN KEY (current_version_id) REFERENCES sop_versions(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- 3. 순서도 노드 (버전에 매달림 → 버전마다 따로, 이전 버전 노드도 남음)
--    content.blocks[type=flowchart].project.nodes[] 를 풀어낸 것
-- ---------------------------------------------------------------------
CREATE TABLE flow_nodes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id  uuid NOT NULL REFERENCES sop_versions(id) ON DELETE CASCADE,
    instance_id text NOT NULL,                            -- 순서도 블록 id (flow_xxx). 한 문서에 순서도 여러 장일 때 구분
    node_key    text NOT NULL,                            -- "seq_3", "decision_1", "sop_2" ...
    node_type   text NOT NULL                             -- start / seq / decision / sop / end
                CHECK (node_type IN ('start', 'seq', 'decision', 'sop', 'end')),
    name        text NOT NULL DEFAULT '',                 -- start/end 표시 이름
    role_owner  text NOT NULL DEFAULT '',                 -- seq: 담당
    action      text NOT NULL DEFAULT '',                 -- seq: Action / decision: 질문
    description text NOT NULL DEFAULT '',
    systems     jsonb NOT NULL DEFAULT '[]',              -- seq: [{"name":"MES","menus":["Lot 조회"]}]
    manual      jsonb NOT NULL DEFAULT '[]',              -- seq: 수동 작업 목록
    ref_sop_no  text NOT NULL DEFAULT '',                 -- sop 노드: 참조 SOP 번호 (지금은 글자로만)
    ref_sop_name text NOT NULL DEFAULT '',
    position    jsonb NOT NULL DEFAULT '{}',              -- {"x": 120, "y": 90}  (1920x1080 캔버스 좌표)
    font_size   int,
    UNIQUE (version_id, instance_id, node_key)
);

-- ---------------------------------------------------------------------
-- 4. 순서도 연결선
--    content.blocks[type=flowchart].project.edges[] 를 풀어낸 것
-- ---------------------------------------------------------------------
CREATE TABLE flow_edges (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id  uuid NOT NULL REFERENCES sop_versions(id) ON DELETE CASCADE,
    instance_id text NOT NULL,
    edge_key    text NOT NULL,                            -- "edge_5"
    source_key  text NOT NULL,                            -- 출발 노드 node_key
    target_key  text NOT NULL,                            -- 도착 노드 node_key
    source_port text NOT NULL DEFAULT '',                 -- top / right / bottom / left
    target_port text NOT NULL DEFAULT '',
    condition   text NOT NULL DEFAULT '',                 -- decision 분기 조건 (Yes / No / 이상 ...)
    line_type   text NOT NULL DEFAULT 'orthogonal',       -- orthogonal / straight
    route       jsonb NOT NULL DEFAULT '{}',              -- 꺾은 선 조절점 {"x":..,"y":..}
    UNIQUE (version_id, instance_id, edge_key)
);

-- ---------------------------------------------------------------------
-- 5. 편집 잠금 (한 번에 한 명만 편집). 하트비트로 expires_at 연장, 만료되면 자동 해제.
-- ---------------------------------------------------------------------
CREATE TABLE sop_edit_locks (
    document_id uuid PRIMARY KEY REFERENCES sop_documents(id) ON DELETE CASCADE,
    locked_by   text NOT NULL,
    locked_at   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);

-- ---------------------------------------------------------------------
-- 6. 자동 임시 저장 (사용자당 문서당 최신 1개만 덮어씀. 버전을 쌓지 않는다.)
--    지금 브라우저 localStorage 임시 저장을 서버로 옮길 때 사용.
-- ---------------------------------------------------------------------
CREATE TABLE sop_drafts (
    document_id uuid NOT NULL REFERENCES sop_documents(id) ON DELETE CASCADE,
    user_id     text NOT NULL,
    content     jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, user_id)
);

-- ---------------------------------------------------------------------
-- 인덱스
-- ---------------------------------------------------------------------
CREATE INDEX ix_documents_area_no   ON sop_documents (area, sop_no);              -- 라이브러리 트리 (AREA > 번호)
CREATE INDEX ix_documents_status    ON sop_documents (status);
CREATE INDEX ix_documents_name_trgm ON sop_documents USING gin (name gin_trgm_ops); -- 이름 부분 검색

CREATE INDEX ix_versions_doc        ON sop_versions (document_id, version_no DESC); -- 버전 이력
CREATE INDEX ix_versions_content    ON sop_versions USING gin (content);            -- JSON 안 검색 (필요 시)

CREATE INDEX ix_flow_nodes_version  ON flow_nodes (version_id);
CREATE INDEX ix_flow_nodes_type     ON flow_nodes (node_type);
CREATE INDEX ix_flow_nodes_role     ON flow_nodes (role_owner);
CREATE INDEX ix_flow_nodes_ref      ON flow_nodes (ref_sop_no) WHERE ref_sop_no <> '';  -- "이 SOP 참조하는 SOP"
CREATE INDEX ix_flow_nodes_action_trgm ON flow_nodes USING gin (action gin_trgm_ops);  -- Action 부분 검색
CREATE INDEX ix_flow_nodes_systems  ON flow_nodes USING gin (systems);                 -- "MES 쓰는 SOP" (@> 조회)

CREATE INDEX ix_flow_edges_version  ON flow_edges (version_id);
CREATE INDEX ix_flow_edges_source   ON flow_edges (version_id, source_key);

CREATE INDEX ix_edit_locks_expires  ON sop_edit_locks (expires_at);

-- ---------------------------------------------------------------------
-- 편집기 JSON ↔ 컬럼 대응 (참고)
-- ---------------------------------------------------------------------
--  doc.sop.id            → sop_documents.sop_no
--  doc.sop.name          → sop_documents.name
--  doc.studio.area       → sop_documents.area
--  doc.studio.revision   → sop_versions.revision
--  doc.studio.owner      → sop_versions.owner
--  doc.studio.tags[]     → sop_versions.tags
--  doc.format / version  → sop_versions.format / format_version
--  doc (전체)            → sop_versions.content
--  doc.blocks[flowchart].instanceId          → flow_nodes.instance_id
--  ...project.nodes[].node / node_type       → flow_nodes.node_key / node_type
--  ...project.nodes[].role_owner / action    → flow_nodes.role_owner / action
--  ...project.nodes[].systems / manual       → flow_nodes.systems / manual
--  ...project.nodes[].sop_id / sop_name      → flow_nodes.ref_sop_no / ref_sop_name
--  ...project.nodes[].x, y                   → flow_nodes.position
--  ...project.edges[].edge / source / target → flow_edges.edge_key / source_key / target_key
--  ...project.edges[].condition / line_type  → flow_edges.condition / line_type

-- ---------------------------------------------------------------------
-- 자주 쓰는 조회 (참고)
-- ---------------------------------------------------------------------
-- 라이브러리 트리:
--   SELECT id, sop_no, name, area, status, updated_at,
--          (SELECT version_no FROM sop_versions v WHERE v.id = d.current_version_id) AS version_no
--     FROM sop_documents d WHERE status <> 'retired' ORDER BY area, sop_no;
--
-- 문서 열기 (최신):
--   SELECT v.content FROM sop_documents d JOIN sop_versions v ON v.id = d.current_version_id WHERE d.id = $1;
--
-- 현재 버전 기준 노드만 (모든 버전이 섞이지 않게 반드시 current_version_id 로 조인):
--   SELECT d.sop_no, n.* FROM flow_nodes n JOIN sop_documents d ON d.current_version_id = n.version_id;
--
-- 나중에 추가할 것 (지금은 보류):
--   flow_nodes.ref_document_id uuid REFERENCES sop_documents(id)   -- 참조를 번호 글자가 아닌 문서 id로
--   sop_documents.review_required boolean                          -- 참조 대상 개정 시 검토 알림
--   sop_chunks (vector / pg_search)                                -- 적재 데이터 + 임베딩 검색

-- SOP-number substring queries need the same trigram support as name queries.
CREATE INDEX ix_documents_no_trgm ON sop_documents USING gin (sop_no gin_trgm_ops);
