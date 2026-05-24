"""Per-user fixed-window rate limiting via Redis.

Used to cap spend on the Claude/Voyage-heavy endpoints (`/ingest`,
`/ask`). One INCR + EXPIRE per request — no Lua, no sliding window — the
edge case (a burst right at a window boundary letting through ~2x the
quota) is acceptable for these caps.

`rate_limit("ingest", "rate_limit_ingest_per_hour")` returns a FastAPI
dependency you attach to a route's `dependencies=[...]` or as a
parameter dep.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Annotated

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis, from_url

from app.config import Settings, get_settings
from app.db.models import User
from app.deps import get_current_user

_redis: Redis | None = None


async def get_redis(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


WINDOW_SECONDS = 3600


def rate_limit(
    bucket: str, limit_attr: str
) -> Callable[..., Coroutine[None, None, None]]:
    """Return a FastAPI dependency enforcing `limit_attr` requests/hour
    per user for the given bucket. `limit_attr` is the `Settings` field
    name so the cap stays env-tunable without redeploying."""

    async def _dep(
        current_user: Annotated[User, Depends(get_current_user)],
        redis: Annotated[Redis, Depends(get_redis)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> None:
        limit = int(getattr(settings, limit_attr))
        if limit <= 0:
            return
        # Window bucket. Truncating to the hour means consecutive requests
        # at e.g. 12:59:59 and 13:00:01 land in different keys — fine for
        # this purpose.
        now = int(time.time())
        window = now // WINDOW_SECONDS
        key = f"rl:{bucket}:{current_user.id}:{window}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, WINDOW_SECONDS)
        if count > limit:
            retry_after = WINDOW_SECONDS - (now % WINDOW_SECONDS)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit}/hour)",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep
