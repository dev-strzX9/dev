import json
import re
from typing import Any
from pydantic import BaseModel, Field, StrictInt, field_validator
from app.config import MAX_CONTENT_BYTES
from app.errors import APIError


def validate_document(doc):
    if doc.get("format") != "sop-editor-mock":
        raise ValueError("doc.format must be sop-editor-mock")
    if type(doc.get("version")) is not int or not -(2**31) <= doc["version"] < 2**31:
        raise ValueError("doc.version must be a 32-bit integer")
    if not isinstance(doc.get("blocks"), list):
        raise ValueError("doc.blocks must be an array")
    sop = doc.get("sop")
    if not isinstance(sop, dict) or not isinstance(sop.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", sop["id"]):
        raise ValueError("doc.sop.id must contain only letters, numbers, -, _ or .")
    try:
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Document must contain valid finite JSON values") from exc
    if b"\\u0000" in raw:
        # PostgreSQL jsonb cannot represent a NUL character.
        def has_nul(value):
            if isinstance(value, str): return "\x00" in value
            if isinstance(value, list): return any(has_nul(v) for v in value)
            if isinstance(value, dict): return any(has_nul(k) or has_nul(v) for k, v in value.items())
            return False
        if has_nul(doc): raise ValueError("Document contains unsupported NUL characters")
    if len(raw) > MAX_CONTENT_BYTES:
        raise APIError(413, "content_too_large", "Document exceeds 20 MiB")
    return doc


class SaveRequest(BaseModel):
    doc: dict[str, Any]
    base_version_no: StrictInt | None = Field(default=None, ge=0)
    change_note: str = Field(default="", max_length=10000, pattern=r"^[^\x00]*$")
    saved_by: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[^\x00]*$")
    _validate_doc = field_validator("doc")(validate_document)


class LockRequest(BaseModel):
    user: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[^\x00]*$")
    ttl_sec: int = Field(default=120, ge=1, le=3600)


class UserRequest(BaseModel):
    user: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[^\x00]*$")


class DraftRequest(UserRequest):
    content: dict[str, Any]
    _validate_content = field_validator("content")(validate_document)
