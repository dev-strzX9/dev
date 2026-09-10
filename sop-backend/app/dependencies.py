from typing import Annotated
from urllib.parse import unquote
from app.errors import APIError
from fastapi import Depends, Header, Request
from psycopg_pool import AsyncConnectionPool


def current_user(x_user: Annotated[str | None, Header()] = None) -> str:
    """Replace this dependency with verified SSO identity when authentication is added."""
    name = unquote(x_user or "").strip() or "anonymous"
    if len(name) > 200 or "\x00" in name:
        raise APIError(422, "validation_error", "Invalid X-User header")
    return name


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


User = Annotated[str, Depends(current_user)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]
