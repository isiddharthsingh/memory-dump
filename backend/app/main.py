from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import ask, ingest, items, search, wiki
from app.queue import close_arq_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_arq_pool()


app = FastAPI(
    title="memory-dump",
    description="Personal memory dump: ingest links, analyze, embed, ask.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(ingest.router)
app.include_router(items.router)
app.include_router(search.router)
app.include_router(ask.router)
app.include_router(wiki.router)
