from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import Item
from app.db.session import get_sessionmaker
from app.notifications import send_to_all

log = logging.getLogger(__name__)


async def weekly_digest(ctx: dict[str, Any]) -> str:
    """Send a single notification summarizing what was saved in the last 7 days."""
    Session = get_sessionmaker()
    since = datetime.now(timezone.utc) - timedelta(days=7)

    async with Session() as session:
        rows = (
            await session.execute(
                select(Item)
                .where(Item.created_at >= since)
                .where(Item.status == "ready")
                .order_by(Item.created_at.desc())
            )
        ).scalars().all()

        if not rows:
            return "no_items"

        count = len(rows)
        top = rows[0]
        title = "Your week in memory-dump"
        body = (
            f"{count} item{'s' if count != 1 else ''} saved this week. "
            f"Most recent: {(top.title or top.url)[:80]}"
        )
        sent = await send_to_all(session, title=title, body=body, data={})

    log.info("weekly_digest: %d items, %d devices", count, sent)
    return f"sent:{sent}"
