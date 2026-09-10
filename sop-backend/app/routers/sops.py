from uuid import UUID
from fastapi import APIRouter, Query
from app.dependencies import Pool, User
from app.db import all_rows, one
from app.errors import APIError
from app.repository import open_document, save_document
from app.schemas import SaveRequest

router = APIRouter(prefix="/api/sops", tags=["sops"])


@router.get("")
async def list_sops(pool: Pool, user: User, status: str = "!retired", q: str = Query(default="", max_length=200), area: str = ""):
    valid = {"draft", "review", "approved", "retired"}
    if status not in valid | {"!retired", "all"} or area not in {"", "P", "E", "D", "T", "C"}:
        raise APIError(422, "validation_error", "Invalid status or area")
    # Treat user wildcards literally. The surrounding % provides substring matching.
    term = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    async with pool.connection() as conn:
        return await all_rows(conn, """SELECT d.id,d.sop_no,d.name,d.area,d.status,v.version_no,v.revision,v.owner,d.updated_at
            FROM sop_documents d JOIN sop_versions v ON v.id=d.current_version_id
            WHERE (%s='all' OR (%s='!retired' AND d.status<>'retired') OR d.status=%s)
            AND (%s='' OR d.area=%s) AND (d.sop_no ILIKE %s OR d.name ILIKE %s)
            ORDER BY d.area,d.sop_no""", (status,status,status,area,area,term,term))


@router.get("/by-no/{sop_no}")
async def get_by_no(sop_no: str, pool: Pool, user: User):
    async with pool.connection() as conn: return await open_document(conn, sop_no=sop_no)


@router.get("/{doc_id}")
async def get_sop(doc_id: UUID, pool: Pool, user: User):
    async with pool.connection() as conn: return await open_document(conn, doc_id=doc_id)


@router.put("/{sop_no}", status_code=201)
async def put_sop(sop_no: str, body: SaveRequest, pool: Pool, user: User):
    return await save_document(pool, sop_no, body, user)


async def change_status(pool, doc_id, status):
    async with pool.connection() as conn:
        row = await one(conn, "UPDATE sop_documents SET status=%s,updated_at=clock_timestamp() WHERE id=%s RETURNING id,status", (status,doc_id))
    if not row: raise APIError(404, "not_found", "SOP not found")
    return row


@router.delete("/{doc_id}")
async def retire(doc_id: UUID, pool: Pool, user: User): return await change_status(pool, doc_id, "retired")


@router.post("/{doc_id}/restore")
async def restore(doc_id: UUID, pool: Pool, user: User): return await change_status(pool, doc_id, "draft")
