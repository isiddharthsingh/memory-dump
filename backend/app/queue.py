from __future__ import annotations

import uuid
from typing import Any

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Job

from app.config import get_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue(
    function: str,
    *args: Any,
    _job_id: str | None = None,
    pool: ArqRedis | None = None,
    **kwargs: Any,
) -> Job | None:
    """Queues a job carrying the current request id, so its logs join the
    request's. With `_job_id`, arq refuses a second job under the same id
    while the first is queued, running, or its result is kept: that's what
    makes requeueing (app/workers/reconcile.py) safe. Returns None when
    refused."""
    pool = pool or await get_arq_pool()
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return await pool.enqueue_job(function, *args, _job_id=_job_id, request_id=request_id, **kwargs)


def ingest_job_id(item_id: uuid.UUID | str, generation: int) -> str:
    return f"ingest:{item_id}:{generation}"


def synthesize_job_id(item_id: uuid.UUID | str, generation: int) -> str:
    return f"synthesize:{item_id}:{generation}"


def forget_job_id(item_id: uuid.UUID | str) -> str:
    return f"forget:{item_id}"


async def enqueue_ingest(
    item_id: uuid.UUID, generation: int, *, upload: bool = False, pool: ArqRedis | None = None
) -> Job | None:
    """Queues the item's ingest for `generation` (Item.ingest_generation)."""
    return await enqueue(
        "ingest_file" if upload else "ingest_item",
        str(item_id),
        generation=generation,
        _job_id=ingest_job_id(item_id, generation),
        pool=pool,
    )
