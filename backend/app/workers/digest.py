from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models import Item
from app.db.session import get_sessionmaker
from app.notifications import send_to_user
from app.queue import enqueue
from app.workers.jobs import job, user_ids

log = logging.getLogger(__name__)

# digest_user keeps its result this long, so its job id (one per user per
# week) refuses a second digest for the same week.
DIGEST_KEEP_RESULT_S = 8 * 24 * 3600


@job
async def weekly_digest(ctx: dict[str, Any]) -> str:
    """Queues digest_user for every user, once per user per ISO week: a run
    that stops halfway and starts again notifies nobody twice."""
    year, week, _ = datetime.now(UTC).isocalendar()
    queued = 0
    async for user_id in user_ids():
        await enqueue(
            "digest_user",
            str(user_id),
            _job_id=f"digest:{user_id}:{year}-W{week:02d}",
            pool=ctx.get("redis"),
        )
        queued += 1
    log.info("weekly_digest: queued %d user(s)", queued)
    return f"queued:{queued}"


@job
async def digest_user(ctx: dict[str, Any], user_id: str) -> str:
    """A notification summarizing the user's last 7 days of saves."""
    uid = uuid.UUID(user_id)
    saved = select(Item).where(
        Item.user_id == uid,
        Item.created_at >= func.now() - timedelta(days=7),
        Item.status == "ready",
    )
    async with get_sessionmaker()() as session:
        count = await session.scalar(select(func.count()).select_from(saved.subquery()))
        if not count:
            return "nothing"
        top = (await session.execute(saved.order_by(Item.created_at.desc()).limit(1))).scalar_one()
        body = (
            f"{count} item{'s' if count != 1 else ''} saved this week. "
            f"Most recent: {(top.title or top.url)[:80]}"
        )
        sent = await send_to_user(
            session, uid, title="Your week in memory-dump", body=body, data={}
        )
    return f"devices:{sent}"
