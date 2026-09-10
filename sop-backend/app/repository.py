import logging
from psycopg.types.json import Jsonb
from app.db import one, all_rows
from app.derive import derive_flow_rows
from app.errors import APIError

log = logging.getLogger(__name__)


async def require_document(conn, doc_id):
    row = await one(conn, "SELECT id, sop_no FROM sop_documents WHERE id=%s", (doc_id,))
    if not row: raise APIError(404, "not_found", "SOP not found")
    return row


async def active_lock(conn, doc_id):
    return await one(conn, "SELECT locked_by, expires_at FROM sop_edit_locks WHERE document_id=%s AND expires_at > clock_timestamp()", (doc_id,))


async def open_document(conn, doc_id=None, sop_no=None, version_no=None):
    # Each lookup uses a fixed SQL statement with bound parameters.
    if sop_no is not None:
        doc = await one(conn, "SELECT id, sop_no, current_version_id FROM sop_documents WHERE sop_no=%s", (sop_no,))
    else:
        doc = await one(conn, "SELECT id, sop_no, current_version_id FROM sop_documents WHERE id=%s", (doc_id,))
    if not doc: raise APIError(404, "not_found", "SOP not found")
    if version_no is None:
        version = await one(conn, "SELECT id AS version_id, version_no, content FROM sop_versions WHERE id=%s AND document_id=%s", (doc["current_version_id"], doc["id"]))
    else:
        version = await one(conn, "SELECT id AS version_id, version_no, content FROM sop_versions WHERE document_id=%s AND version_no=%s", (doc["id"], version_no))
    if not version: raise APIError(404, "not_found", "Version not found")
    return {"id": doc["id"], "sop_no": doc["sop_no"], **version, "lock": await active_lock(conn, doc["id"])}


def metadata(doc):
    warnings = []
    studio = doc.get("studio")
    if not isinstance(studio, dict): studio = {}
    area = studio.get("area", "")
    if not isinstance(area, str) or area not in {"", "P", "E", "D", "T", "C"}:
        area = ""
        warnings.append("studio.area is invalid; stored metadata area as empty")
    def text(value, field):
        if isinstance(value, str): return value
        warnings.append(f"{field} is not text; stored metadata as empty")
        return ""
    tags = studio.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        warnings.append("studio.tags is invalid; stored metadata tags as empty")
        tags = []
    return {"name": text(doc["sop"].get("name", ""), "sop.name"), "area": area,
            "revision": text(studio.get("revision", ""), "studio.revision"),
            "owner": text(studio.get("owner", ""), "studio.owner"), "tags": tags}, warnings


async def save_document(pool, sop_no, body, user):
    if sop_no != body.doc["sop"]["id"]:
        raise APIError(400, "sop_no_mismatch", "Path SOP number and doc.sop.id differ")
    # Derive and normalize before acquiring the transaction lock.
    nodes, edges = derive_flow_rows(body.doc)
    meta, warnings = metadata(body.doc)
    warnings.extend(nodes.warnings + edges.warnings)
    actor = body.saved_by or user
    async with pool.connection() as conn:
        async with conn.transaction():
            # ON CONFLICT serializes concurrent first inserts on the unique SOP number.
            await conn.execute("INSERT INTO sop_documents(sop_no,created_by) VALUES(%s,%s) ON CONFLICT(sop_no) DO NOTHING", (sop_no, actor))
            document = await one(conn, "SELECT id FROM sop_documents WHERE sop_no=%s FOR UPDATE", (sop_no,))
            doc_id = document["id"]
            # Aggregate queries cannot use FOR UPDATE in PostgreSQL: lock the parent above.
            latest = (await one(conn, "SELECT COALESCE(MAX(version_no),0) AS n FROM sop_versions WHERE document_id=%s", (doc_id,)))["n"]
            if body.base_version_no is not None and body.base_version_no != latest:
                raise APIError(409, "version_conflict", "Another version has been saved. Reload before saving.", current_version_no=latest)
            version = await one(conn, """INSERT INTO sop_versions(document_id,version_no,format,format_version,content,revision,owner,tags,change_note,saved_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id AS version_id,version_no,saved_at""",
                (doc_id, latest+1, body.doc["format"], body.doc["version"], Jsonb(body.doc), meta["revision"], meta["owner"], meta["tags"], body.change_note, actor))
            async with conn.cursor() as cur:
                if nodes:
                    await cur.executemany("""INSERT INTO flow_nodes(version_id,instance_id,node_key,node_type,name,role_owner,action,description,systems,manual,ref_sop_no,ref_sop_name,position,font_size)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [(version["version_id"], n["instance_id"], n["node_key"], n["node_type"], n["name"], n["role_owner"], n["action"], n["description"], Jsonb(n["systems"]), Jsonb(n["manual"]), n["ref_sop_no"], n["ref_sop_name"], Jsonb(n["position"]), n["font_size"]) for n in nodes])
                if edges:
                    await cur.executemany("""INSERT INTO flow_edges(version_id,instance_id,edge_key,source_key,target_key,source_port,target_port,condition,line_type,route)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        [(version["version_id"], e["instance_id"], e["edge_key"], e["source_key"], e["target_key"], e["source_port"], e["target_port"], e["condition"], e["line_type"], Jsonb(e["route"])) for e in edges])
            await conn.execute("UPDATE sop_documents SET name=%s,area=%s,updated_at=clock_timestamp(),current_version_id=%s WHERE id=%s", (meta["name"], meta["area"], version["version_id"], doc_id))
            await conn.execute("DELETE FROM sop_drafts WHERE document_id=%s AND user_id=%s", (doc_id, actor))
            lock = await active_lock(conn, doc_id)
    result = {"id": doc_id, "sop_no": sop_no, **version, "warnings": warnings}
    if lock and lock["locked_by"] != actor:
        result["warning"] = {"code": "locked_by_other", **lock}
    log.info("save sop_no=%s version_no=%s nodes=%s", sop_no, version["version_no"], len(nodes))
    return result
