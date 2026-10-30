"""How every job runs: its log context and metrics (`job`), when it runs
again, and when it gives up.

A job meets a transient failure (app/errors.py) by raising `later(...)`: arq
runs it again after an exponential backoff with jitter, or after the wait the
provider asked for. arq drops a job past MAX_TRIES without running it, which
would leave an item "pending" for good, so on its last try (`last_try`) a job
records the failure where someone will see it instead: the item turns
"failed", or its wiki status does. Failures from the site being saved get
SITE_MAX_TRIES.
"""

from __future__ import annotations

import functools
import random
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select

from app.db.models import User
from app.db.session import get_sessionmaker
from app.errors import from_site, retry_after
from app.metrics import JOB_SECONDS, JOBS

MAX_TRIES = 8
SITE_MAX_TRIES = 3
# Longest wait between tries.
MAX_DELAY_S = 30 * 60
# arq cancels a job that runs longer.
JOB_TIMEOUT_S = 600


def last_try(ctx: dict[str, Any], exc: BaseException | None = None) -> bool:
    tries = SITE_MAX_TRIES if exc is not None and from_site(exc) else MAX_TRIES
    return int(ctx.get("job_try", 1)) >= tries


def later(ctx: dict[str, Any], first: float, exc: BaseException | None = None) -> Retry:
    """Runs the job again after `first` seconds, doubling per try up to
    MAX_DELAY_S, each wait drawn between half and all of that so jobs that
    failed together don't all come back at once."""
    ceiling = min(first * 2 ** (int(ctx.get("job_try", 1)) - 1), MAX_DELAY_S)
    delay = random.uniform(ceiling / 2, ceiling)
    asked = retry_after(exc) if exc is not None else None
    return Retry(defer=max(delay, min(asked or 0, MAX_DELAY_S)))


def job[R](fn: Callable[..., Coroutine[Any, Any, R]]) -> Callable[..., Coroutine[Any, Any, R]]:
    """Binds the job's name, id, try and the request that queued it to every
    log line it writes, and counts how it ended."""

    @functools.wraps(fn)
    async def run(
        ctx: dict[str, Any], *args: Any, request_id: str | None = None, **kwargs: Any
    ) -> R:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            job=fn.__name__,
            job_id=ctx.get("job_id"),
            job_try=ctx.get("job_try"),
            request_id=request_id,
        )
        start = time.monotonic()
        outcome = "error"
        try:
            result = await fn(ctx, *args, **kwargs)
            outcome = "ok"
            return result
        except Retry:
            outcome = "retry"
            raise
        finally:
            JOBS.labels(fn.__name__, outcome).inc()
            JOB_SECONDS.labels(fn.__name__).observe(time.monotonic() - start)

    return run


async def user_ids(batch: int = 1000) -> AsyncIterator[uuid.UUID]:
    """Every user id, read a batch at a time, for the jobs that fan out per
    user."""
    last: uuid.UUID | None = None
    while True:
        stmt = select(User.id).order_by(User.id).limit(batch)
        if last is not None:
            stmt = stmt.where(User.id > last)
        async with get_sessionmaker()() as session:
            ids = list((await session.execute(stmt)).scalars())
        for user_id in ids:
            yield user_id
        if len(ids) < batch:
            return
        last = ids[-1]
