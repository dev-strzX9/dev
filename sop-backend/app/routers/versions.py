from uuid import UUID
from fastapi import APIRouter, Path
from app.dependencies import Pool, User
from app.db import all_rows
from app.repository import require_document, open_document

router = APIRouter(prefix="/api/sops/{doc_id}/versions", tags=["versions"])


@router.get("")
async def versions(doc_id: UUID, pool: Pool, user: User):
    async with pool.connection() as conn:
        await require_document(conn, doc_id)
        return await all_rows(conn, "SELECT version_no,revision,saved_by,saved_at,change_note FROM sop_versions WHERE document_id=%s ORDER BY version_no DESC", (doc_id,))


@router.get("/{version_no}")
async def version(doc_id: UUID, pool: Pool, user: User, version_no: int = Path(ge=1)):
    async with pool.connection() as conn: return await open_document(conn, doc_id=doc_id, version_no=version_no)


@router.get("/{a}/diff/{b}")
async def diff(doc_id: UUID, pool: Pool, user: User, a: int = Path(ge=1), b: int = Path(ge=1)):
    async with pool.connection() as conn:
        # Existence checks distinguish missing versions from versions with zero nodes.
        first = await open_document(conn, doc_id=doc_id, version_no=a)
        second = await open_document(conn, doc_id=doc_id, version_no=b)
        rows_a = await all_rows(conn, "SELECT * FROM flow_nodes WHERE version_id=%s ORDER BY instance_id,node_key", (first["version_id"],))
        rows_b = await all_rows(conn, "SELECT * FROM flow_nodes WHERE version_id=%s ORDER BY instance_id,node_key", (second["version_id"],))
    def keyed(rows): return {(r["instance_id"], r["node_key"]): {k:v for k,v in r.items() if k not in {"id","version_id"}} for r in rows}
    old, new = keyed(rows_a), keyed(rows_b)
    return {"from_version": a, "to_version": b,
            "added": [new[k] for k in sorted(new.keys()-old.keys())],
            "deleted": [old[k] for k in sorted(old.keys()-new.keys())],
            "modified": [{"before":old[k],"after":new[k]} for k in sorted(old.keys() & new.keys()) if old[k]!=new[k]]}
