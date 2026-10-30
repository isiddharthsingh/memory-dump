import asyncio
import logging
import math
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from supertokens_python.framework.fastapi import get_middleware
from voyageai import error as voyage_error

from app import kv, logging_setup  # noqa: F401  — configures stdlib + structlog
from app.api import ask, auth, chats, devices, ingest, items, search, wiki
from app.auth.rate_limit import AuthRateLimit
from app.auth.supertokens import init_supertokens
from app.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.errors import ProviderUnavailable, is_transient, retry_after, user_message
from app.metrics import render
from app.observability import BodyLimit, MinAppBuild, RequestContext, init_sentry
from app.queue import close_arq_pool

log = logging.getLogger(__name__)

init_sentry("api")
init_supertokens(get_settings())

# The largest JSON body the app sends is a long saved chat.
MAX_BODY_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await close_arq_pool()
    await kv.close()
    await get_engine().dispose()


app = FastAPI(
    title="memory-dump",
    description="Personal memory dump: ingest links, analyze, embed, ask.",
    version="0.1.0",
    lifespan=lifespan,
)
# The last added runs first: request ids and metrics see every request, and
# the body cap, the oldest-build check and the sign-in limit all come before
# SuperTokens' /auth routes and the 401s from verify_session().
app.add_middleware(get_middleware())
app.add_middleware(AuthRateLimit)
app.add_middleware(MinAppBuild)
app.add_middleware(
    BodyLimit,
    default=MAX_BODY_BYTES,
    by_path={
        path: ingest.MAX_UPLOAD_BYTES + 1024 * 1024 for path in ("/ingest/file", "/v1/ingest/file")
    },
)
app.add_middleware(RequestContext)


@app.get("/livez", tags=["meta"])
@app.get("/healthz", tags=["meta"])
async def livez() -> dict[str, str]:
    """The process answers. Says nothing about what it depends on."""
    return {"status": "ok"}


@app.get("/readyz", tags=["meta"])
async def readyz() -> JSONResponse:
    """Postgres and Redis answer within 2 s each; 503 naming the ones that
    don't, so a load balancer stops sending requests here meanwhile."""
    failing = []
    try:
        async with asyncio.timeout(2), get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        failing.append("postgres")
    try:
        async with asyncio.timeout(2):
            await kv.redis().ping()
    except Exception:
        failing.append("redis")
    if failing:
        return JSONResponse({"status": "unavailable", "failing": failing}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    token = get_settings().metrics_token
    sent = request.headers.get("authorization", "")
    if not token or not secrets.compare_digest(sent, f"Bearer {token}"):
        raise HTTPException(status_code=404)
    body, content_type = render()
    return Response(body, media_type=content_type)


async def _upstream_failed(request: Request, exc: Exception) -> Response:
    """Claude or Voyage failed under a request: 503 with Retry-After when
    trying again later can work, 502 when it can't. Never their error text."""
    if is_transient(exc):
        wait = math.ceil(retry_after(exc) or 30)
        log.warning("upstream unavailable: %r", exc)
        return JSONResponse(
            {"detail": user_message(exc)}, status_code=503, headers={"Retry-After": str(wait)}
        )
    log.error("upstream call failed", exc_info=exc)
    return JSONResponse({"detail": user_message(exc)}, status_code=502)


for error in (ProviderUnavailable, anthropic.APIError, voyage_error.VoyageError):
    app.add_exception_handler(error, _upstream_failed)

for router in (
    auth.router,
    ingest.router,
    items.router,
    search.router,
    ask.router,
    chats.router,
    wiki.router,
    devices.router,
):
    app.include_router(router, prefix="/v1")
    # The same routes unversioned, for app builds from before /v1.
    app.include_router(router, include_in_schema=False)
