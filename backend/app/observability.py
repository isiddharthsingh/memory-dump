"""What wraps every request: its id, its access log line and metrics
(`RequestContext`), a cap on its body (`BodyLimit`), and the oldest app build
served (`MinAppBuild`). Plus Sentry, for the API and the worker."""

from __future__ import annotations

import re
import time
import uuid

import sentry_sdk
import structlog
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.metrics import HTTP_REQUESTS, HTTP_SECONDS

access = structlog.get_logger("app.access")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def init_sentry(component: str) -> None:
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    integrations = []
    if component == "worker":
        from sentry_sdk.integrations.arq import ArqIntegration

        integrations.append(ArqIntegration())
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        server_name=component,
        integrations=integrations,
        send_default_pii=False,
    )


class RequestContext:
    """Gives each request an id (the client's X-Request-ID when it sent a
    sane one), binds it to every log line written while answering, returns
    it in X-Request-ID, and logs and counts the request by route template
    (never the raw path, whose ids would make every request its own series)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sent = Headers(scope=scope).get("x-request-id", "")
        request_id = sent if _REQUEST_ID_RE.match(sent) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.monotonic()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            elapsed = time.monotonic() - start
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status)).inc()
            HTTP_SECONDS.labels(method, route).observe(elapsed)
            access.info(
                "request", method=method, route=route, status=status, ms=round(elapsed * 1000)
            )


class BodyLimit:
    """Refuses a request body over its route's cap with 413, by its
    Content-Length or, when it has none, once that many bytes have arrived.
    Starlette would otherwise read a multipart upload of any size to disk
    before the route could look at it."""

    def __init__(self, app: ASGIApp, default: int, by_path: dict[str, int]) -> None:
        self.app = app
        self.default = default
        self.by_path = by_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.by_path.get(scope["path"], self.default)
        length = Headers(scope=scope).get("content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            await _too_large(limit)(scope, receive, send)
            return

        received = 0

        async def capped() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge(limit)
            return message

        try:
            await self.app(scope, capped, send)
        except _TooLarge:
            await _too_large(limit)(scope, receive, send)


class _TooLarge(HTTPException):
    """An HTTPException, so FastAPI's body parsing passes it on as a 413
    instead of turning it into a 400; BodyLimit answers it for routes outside
    FastAPI (SuperTokens')."""

    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=f"Request body over {limit} bytes")


def _too_large(limit: int) -> JSONResponse:
    return JSONResponse({"detail": f"Request body over {limit} bytes"}, status_code=413)


class MinAppBuild:
    """Answers 426 to app builds older than MIN_APP_BUILD, which the app
    shows as "update the app". Builds send X-App-Build; builds from before
    the /v1 prefix send nothing and only call unversioned routes, so with a
    minimum set those are refused too. Health, metrics, docs and SuperTokens'
    /auth routes stay open."""

    OPEN = ("/healthz", "/livez", "/readyz", "/metrics", "/docs", "/openapi.json", "/auth/")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        minimum = get_settings().min_app_build
        if scope["type"] != "http" or minimum <= 0 or scope["path"].startswith(self.OPEN):
            await self.app(scope, receive, send)
            return
        build = Headers(scope=scope).get("x-app-build", "")
        versioned = scope["path"].startswith("/v1/")
        too_old = int(build) < minimum if build.isdigit() else not versioned
        if too_old:
            response = JSONResponse(
                {"detail": "This version of the app is too old. Update it to keep going."},
                status_code=426,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
