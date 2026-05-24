"""Expo push notifications.

Pushes through Expo's hosted relay (https://exp.host/--/api/v2/push/send).
On iOS this requires a development build with a push-capable provisioning
profile — a paid Apple Developer account. Free accounts get a token but
delivery silently fails.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device

log = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_to_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> int:
    """Fan a single notification out to every device registered to `user_id`."""
    rows = (
        await session.execute(
            select(Device.expo_push_token).where(Device.user_id == user_id)
        )
    ).scalars().all()
    if not rows:
        return 0

    messages = [
        {
            "to": tok,
            "title": title,
            "body": body,
            "data": data or {},
            "sound": "default",
        }
        for tok in rows
    ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            r.raise_for_status()
    except Exception:
        log.exception("expo push send failed")
        return 0
    return len(messages)
