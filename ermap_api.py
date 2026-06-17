"""FastAPI app entrypoint."""

from fastapi import FastAPI

from ermap_router import router

app = FastAPI(title="ER MAP Agent API")
app.include_router(router)
