"""Puts back what the queue lost, every 5 minutes (`requeue_stuck`), and
reports the queue's depth every minute (`observe_queue`).

The API commits an item and then queues its job, so a crash in between, or
Redis losing a queued job (a restart between AOF syncs, a flush), leaves an
item that nothing will process. Ingest and synthesis jobs have ids made of the
item and its generation (app/queue.py), and arq refuses a job whose id is
still queued, deferred, running or holding its result, so queueing one again
only ever adds what's missing.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from arq.constants import default_queue_name
from sqlalchemy import delete, func, select, update

from app.db.models import TOMBSTONE_DAYS, Item, ItemTombstone
from app.db.session import get_sessionmaker
from app.metrics import QUEUE_JOBS, QUEUE_OLDEST_SECONDS
from app.queue import enqueue, enqueue_ingest, synthesize_job_id
from app.storage import get_storage
from app.workers.jobs import JOB_TIMEOUT_S, job

log = logging.getLogger(__name__)

# A pending item untouched this long may have lost its job. Jobs deferred
# for longer still exist, and queueing them again is refused.
PENDING_AFTER = timedelta(minutes=10)
# A job "processing" this long was cancelled at JOB_TIMEOUT_S, or its worker
# died and nothing ran it again.
PROCESSING_AFTER = timedelta(seconds=JOB_TIMEOUT_S + 300)
WIKI_PENDING_AFTER = timedelta(minutes=30)
# An upload queued by an API from before uploads were kept by key: its path
# lived only in the job, so once that's gone too long there's nothing to read.
LEGACY_UPLOAD_AFTER = timedelta(days=1)
BATCH = 500


@job
async def requeue_stuck(ctx: dict[str, Any]) -> str:
    pool = ctx.get("redis")
    async with get_sessionmaker()() as session:
        pending = (
            await session.execute(
                select(Item.id, Item.ingest_generation, Item.url, Item.upload_key, Item.updated_at)
                .where(Item.status == "pending", Item.updated_at < func.now() - PENDING_AFTER)
                .order_by(Item.updated_at)
                .limit(BATCH)
            )
        ).all()
        timed_out = (
            (
                await session.execute(
                    update(Item)
                    .where(
                        Item.status == "processing", Item.updated_at < func.now() - PROCESSING_AFTER
                    )
                    .values(status="failed", error="TimeoutError: saving it took too long")
                    .returning(Item.id)
                )
            )
            .scalars()
            .all()
        )
        lost_uploads = (
            (
                await session.execute(
                    update(Item)
                    .where(
                        Item.status == "pending",
                        Item.url.startswith("file://"),
                        Item.upload_key.is_(None),
                        Item.updated_at < func.now() - LEGACY_UPLOAD_AFTER,
                    )
                    .values(status="failed", error="FileNotFoundError: the upload was lost")
                    .returning(Item.id)
                )
            )
            .scalars()
            .all()
        )
        unsynthesized = (
            await session.execute(
                select(Item.id, Item.ingest_generation)
                .where(
                    Item.wiki_status == "pending",
                    Item.status == "ready",
                    Item.updated_at < func.now() - WIKI_PENDING_AFTER,
                )
                .limit(BATCH)
            )
        ).all()
        tombstones = (
            (
                await session.execute(
                    delete(ItemTombstone)
                    .where(ItemTombstone.deleted_at < func.now() - timedelta(days=TOMBSTONE_DAYS))
                    .returning(ItemTombstone.item_id)
                )
            )
            .scalars()
            .all()
        )
        await session.commit()

    requeued = 0
    for item_id, generation, url, upload_key, _ in pending:
        upload = url.startswith("file://")
        if upload and upload_key is None:
            continue
        if await enqueue_ingest(item_id, generation, upload=upload, pool=pool) is not None:
            requeued += 1
    resynthesized = 0
    for item_id, generation in unsynthesized:
        job_id = synthesize_job_id(item_id, generation)
        if await enqueue("synthesize_topics", str(item_id), _job_id=job_id, pool=pool) is not None:
            resynthesized += 1
    swept = await get_storage().sweep()

    summary = (
        f"requeued:{requeued},timed_out:{len(timed_out)},lost_uploads:{len(lost_uploads)},"
        f"resynthesized:{resynthesized},tombstones:{len(tombstones)},uploads:{swept}"
    )
    if requeued or timed_out or lost_uploads or resynthesized:
        log.warning("reconcile: %s", summary)
    return summary


async def observe_queue(ctx: dict[str, Any]) -> None:
    redis = ctx["redis"]
    QUEUE_JOBS.set(await redis.zcard(default_queue_name))
    # Scores are when each job is due; the lowest is the one waiting longest.
    oldest = await redis.zrange(default_queue_name, 0, 0, withscores=True)
    waited = time.time() - oldest[0][1] / 1000 if oldest else 0
    QUEUE_OLDEST_SECONDS.set(max(waited, 0))
