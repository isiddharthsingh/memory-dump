from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models import Item, User
from app.db.session import get_sessionmaker
from app.notifications import send_to_user

log = logging.getLogger(__name__)


async def weekly_digest(ctx: dict[str, Any]) -> str:
    """Per-user notification summarizing the last 7 days of saves."""
    Session = get_sessionmaker()
    since = datetime.now(UTC) - timedelta(days=7)

    total_users = 0
    total_devices = 0
    async with Session() as session:
        user_ids = (await session.execute(select(User.id))).scalars().all()

        for user_id in user_ids:
            rows = (
                await session.execute(
                    select(Item)
                    .where(Item.user_id == user_id)
                    .where(Item.created_at >= since)
                    .where(Item.status == "ready")
                    .order_by(Item.created_at.desc())
                )
            ).scalars().all()

            if not rows:
                continue

            count = len(rows)
            top = rows[0]
            title = "Your week in memory-dump"
            body = (
                f"{count} item{'s' if count != 1 else ''} saved this week. "
                f"Most recent: {(top.title or top.url)[:80]}"
            )
            sent = await send_to_user(session, user_id, title=title, body=body, data={})
            total_users += 1
            total_devices += sent

    log.info(
        "weekly_digest: notified %d user(s) across %d device(s)",
        total_users,
        total_devices,
    )
    return f"users:{total_users},devices:{total_devices}"
