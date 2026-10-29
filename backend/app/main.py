from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from supertokens_python.framework.fastapi import get_middleware

import app.logging_setup  # noqa: F401  — configures stdlib + structlog
from app.api import ask, auth, chats, devices, ingest, items, search, wiki
from app.auth.rate_limit import close_redis
from app.auth.supertokens import init_supertokens
from app.config import get_settings
from app.db.session import get_engine
from app.queue import close_arq_pool

init_supertokens(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_arq_pool()
    await close_redis()
    await get_engine().dispose()


app = FastAPI(
    title="memory-dump",
    description="Personal memory dump: ingest links, analyze, embed, ask.",
    version="0.1.0",
    lifespan=lifespan,
)
# SuperTokens' /auth routes, and the 401s from verify_session().
app.add_middleware(get_middleware())


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(items.router)
app.include_router(search.router)
app.include_router(ask.router)
app.include_router(chats.router)
app.include_router(wiki.router)
app.include_router(devices.router)
