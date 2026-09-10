from uuid import UUID
from fastapi import APIRouter, Query
from psycopg.types.json import Jsonb
from app.dependencies import Pool, User
from app.db import one
from app.errors import APIError
from app.repository import require_document
from app.schemas import DraftRequest

router = APIRouter(prefix="/api/sops/{doc_id}/draft", tags=["drafts"])


@router.put("")
async def put_draft(doc_id: UUID, body: DraftRequest, pool: Pool, user: User):
    async with pool.connection() as conn:
        doc = await require_document(conn, doc_id)
        if doc["sop_no"] != body.content["sop"]["id"]:
            raise APIError(400, "sop_no_mismatch", "Draft SOP number differs from document")
        return await one(conn, """INSERT INTO sop_drafts(document_id,user_id,content) VALUES(%s,%s,%s)
            ON CONFLICT(document_id,user_id) DO UPDATE SET content=EXCLUDED.content,updated_at=clock_timestamp()
            RETURNING document_id,user_id,updated_at""", (doc_id,body.user or user,Jsonb(body.content)))


@router.get("")
async def get_draft(doc_id: UUID, pool: Pool, current: User, user: str | None = Query(default=None, max_length=200)):
    async with pool.connection() as conn:
        await require_document(conn, doc_id)
        row = await one(conn, "SELECT content,updated_at FROM sop_drafts WHERE document_id=%s AND user_id=%s", (doc_id,user or current))
    if not row: raise APIError(404, "not_found", "Draft not found")
    return row


@router.delete("")
async def delete_draft(doc_id: UUID, pool: Pool, current: User, user: str | None = Query(default=None, max_length=200)):
    async with pool.connection() as conn:
        await require_document(conn, doc_id)
        await conn.execute("DELETE FROM sop_drafts WHERE document_id=%s AND user_id=%s", (doc_id,user or current))
    return {"deleted": True}
