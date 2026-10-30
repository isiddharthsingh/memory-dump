"""The shared Redis client for rate limits, caches and provider slots. The job
queue has its own connection (app/queue.py)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError

from app.config import get_settings

log = logging.getLogger(__name__)

_client: tuple[asyncio.AbstractEventLoop, Redis] | None = None


def redis() -> Redis:
    """One client per event loop: the API and the worker each run one, the
    tests start several. Short socket timeouts, since every caller here would
    rather go on without Redis than wait for it."""
    global _client
    loop = asyncio.get_running_loop()
    if _client is None or _client[0] is not loop:
        _client = (
            loop,
            from_url(
                get_settings().redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            ),
        )
    return _client[1]


async def close() -> None:
    global _client
    if _client is not None:
        await _client[1].aclose()
        _client = None


def tags_key(user_id: uuid.UUID) -> str:
    """The user's tag counts (GET /items/tags)."""
    return f"tags:{user_id}"


async def drop(key: str) -> None:
    """Deletes a cached value. Best effort: one that outlives this expires."""
    try:
        await redis().delete(key)
    except RedisError:
        log.warning("couldn't drop %s from the cache", key, exc_info=True)
