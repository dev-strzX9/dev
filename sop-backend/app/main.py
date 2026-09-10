import logging
from contextlib import asynccontextmanager
from time import perf_counter
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from app.config import Settings, MAX_REQUEST_BYTES
from app.db import make_pool
from app.dependencies import Pool, User
from app.errors import APIError
from app.routers import sops, versions, locks, drafts

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class RequestMiddleware:
    """Cap streamed bodies before JSON decoding, including chunked requests."""
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http": return await self.app(scope, receive, send)
        start, status = perf_counter(), 500
        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-content-type-options", b"nosniff"))
            await send(message)
        try:
            if scope["method"] in {"PUT", "POST", "PATCH", "DELETE"}:
                chunks, size = [], 0
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect": return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > MAX_REQUEST_BYTES:
                        return await JSONResponse({"error":{"code":"content_too_large","message":"Request exceeds transport limit"}}, status_code=413)(scope,receive,tracked_send)
                    chunks.append(chunk)
                    if not message.get("more_body", False): break
                buffered = b"".join(chunks)
                replayed = False
                async def replay():
                    nonlocal replayed
                    if replayed: return await receive()
                    replayed = True
                    return {"type":"http.request","body":buffered,"more_body":False}
                await self.app(scope, replay, tracked_send)
            else:
                await self.app(scope, receive, tracked_send)
        finally:
            log.info("%s %s %s %.1fms", scope["method"], scope["path"], status, (perf_counter()-start)*1000)


def create_app(settings=None):
    settings = settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        pool = make_pool(settings.database_url)
        app.state.pool = pool
        try:
            await pool.open(wait=True, timeout=15)
            yield
        finally:
            await pool.close()

    app = FastAPI(title="SOP Studio API", lifespan=lifespan)
    app.add_middleware(RequestMiddleware)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["GET","PUT","POST","DELETE"], allow_headers=["Content-Type","X-User"])

    @app.exception_handler(APIError)
    async def api_error(request, exc): return JSONResponse(jsonable_encoder({"error":exc.error}), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo submitted documents, HTML or images in error responses.
        details = [{"loc":list(e["loc"]), "message":e["msg"]} for e in exc.errors()]
        return JSONResponse({"error":{"code":"validation_error","message":"Invalid request","details":details}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error":{"code":"not_found" if exc.status_code==404 else "http_error","message":str(exc.detail)}}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        log.exception("Unhandled request error")
        return JSONResponse({"error":{"code":"internal_error","message":"Internal server error"}}, status_code=500)

    @app.get("/api/health")
    async def health(pool: Pool, user: User):
        try:
            async with pool.connection() as conn: await conn.execute("SELECT 1")
        except Exception:
            log.exception("Database health check failed")
            raise APIError(503, "db_unavailable", "Database unavailable")
        return {"ok":True,"db":"up"}

    for router in (sops.router, versions.router, locks.router, drafts.router): app.include_router(router)

    @app.get("/", include_in_schema=False)
    async def index():
        for name in ("SOP_STUDIO_FINAL.html", "SOP_EXPORT_3.html"):
            path = settings.static_dir / name
            if path.is_file(): return FileResponse(path)
        raise APIError(404, "static_not_found", "Editor HTML is missing from STATIC_DIR")

    app.mount("/static", StaticFiles(directory=settings.static_dir, check_dir=False), name="static")
    return app


app = create_app()
