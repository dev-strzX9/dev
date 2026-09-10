from uuid import UUID
from fastapi import APIRouter
from app.dependencies import Pool, User
from app.db import one
from app.errors import APIError
from app.repository import require_document, active_lock
from app.schemas import LockRequest, UserRequest

router = APIRouter(prefix="/api/sops/{doc_id}/lock", tags=["locks"])


@router.post("")
async def acquire(doc_id: UUID, body: LockRequest, pool: Pool, user: User):
    actor = body.user or user
    async with pool.connection() as conn:
        await require_document(conn, doc_id)
        row = await one(conn, """INSERT INTO sop_edit_locks(document_id,locked_by,expires_at)
            VALUES(%s,%s,clock_timestamp()+%s*interval '1 second')
            ON CONFLICT(document_id) DO UPDATE SET locked_by=EXCLUDED.locked_by,locked_at=clock_timestamp(),
                expires_at=clock_timestamp()+%s*interval '1 second'
            WHERE sop_edit_locks.locked_by=EXCLUDED.locked_by OR sop_edit_locks.expires_at<=clock_timestamp()
            RETURNING locked_by,expires_at""", (doc_id,actor,body.ttl_sec,body.ttl_sec))
        if not row:
            lock = await active_lock(conn, doc_id)
            raise APIError(423, "locked", "Another user holds the edit lock", **(lock or {}))
    return row


@router.delete("")
async def release(doc_id: UUID, body: UserRequest, pool: Pool, user: User):
    async with pool.connection() as conn:
        await require_document(conn, doc_id)
        row = await one(conn, "DELETE FROM sop_edit_locks WHERE document_id=%s AND locked_by=%s RETURNING document_id", (doc_id,body.user or user))
    return {"released": bool(row)}
