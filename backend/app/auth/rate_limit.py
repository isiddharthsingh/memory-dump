"""Token buckets in Redis.

Per user, they cap spend on the endpoints that call Claude or Voyage
(`rate_limit(...)`, a FastAPI dependency); per client address, they cap
password guessing on sign-in and sign-up (`AuthRateLimit`). A bucket holds an
hour's allowance and refills at the same rate, so a burst can use it all at
once and then gets one more request every 3600/limit seconds. One Lua script
takes a token atomically, on Redis's clock, so API processes on different hosts
share each bucket.

When Redis can't be reached the request goes through, and that's logged: a
quota isn't worth an outage.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import Depends, HTTPException, status
from redis.exceptions import RedisError
from starlette.datastructures import URL
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from supertokens_python.normalised_url_path import NormalisedURLPath

from app import kv
from app.config import Settings, get_settings
from app.db.models import User
from app.deps import get_current_user

log = logging.getLogger(__name__)

WINDOW_MS = 3_600_000

_TAKE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local capacity = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * capacity / window)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = math.ceil((1 - tokens) * window / capacity)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], window)
return wait
"""


async def take(key: str, per_hour: int) -> float:
    """Takes one request from `key`'s bucket: 0 when it may go ahead, else
    the seconds until it may."""
    if per_hour <= 0:
        return 0
    try:
        script = kv.redis().register_script(_TAKE)
        wait_ms = await script(keys=[key], args=[per_hour, WINDOW_MS])
    except RedisError:
        log.warning("rate limit unavailable, letting %s through", key, exc_info=True)
        return 0
    return int(wait_ms) / 1000


def rate_limit(bucket: str, limit_attr: str) -> Callable[..., Coroutine[None, None, None]]:
    """Return a FastAPI dependency enforcing `limit_attr` requests/hour
    per user for the given bucket. `limit_attr` is the `Settings` field
    name so the cap stays env-tunable without redeploying."""

    async def _dep(
        current_user: Annotated[User, Depends(get_current_user)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> None:
        limit = int(getattr(settings, limit_attr))
        wait = await take(f"rl:{bucket}:{current_user.id}", limit)
        if wait > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit}/hour)",
                headers={"Retry-After": str(math.ceil(wait))},
            )

    return _dep


# SuperTokens' routes that check a password. It also answers them with a
# trailing slash and under a tenant (/auth/public/signin), and the dashboard
# signs in at /auth/dashboard/api/signin.
AUTH_PATH = re.compile(r"/auth(?:/.+)?/sign(?:in|up)/*")


def is_auth_path(scope: Scope) -> bool:
    """Judged on the path as SuperTokens' middleware normalises it before
    routing (surrounding whitespace and `;params` dropped), so no spelling it
    routes to a sign-in gets past the limit."""
    path = NormalisedURLPath(URL(scope=scope).path).get_as_string_dangerous()
    return AUTH_PATH.fullmatch(path) is not None


class AuthRateLimit:
    """Caps sign-in and sign-up attempts per client address. SuperTokens
    serves those routes from its own middleware, so this sits in front of it.
    Behind a proxy, uvicorn takes the address from X-Forwarded-For when the
    proxy's own address is in FORWARDED_ALLOW_IPS."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and is_auth_path(scope):
            client = scope.get("client")
            address = client[0] if client else "unknown"
            wait = await take(f"rl:auth:{address}", get_settings().rate_limit_auth_per_hour)
            if wait > 0:
                response = JSONResponse(
                    {"detail": "Too many attempts. Try again later."},
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    headers={"Retry-After": str(math.ceil(wait))},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
