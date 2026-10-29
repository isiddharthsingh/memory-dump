"""APNs push notifications.

Sends straight to Apple Push Notification service over HTTP/2, signed with a
token-based (.p8) provider key. That needs a paid Apple Developer account;
when APNS_KEY_PATH is unset, pushes are skipped.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import Device

log = logging.getLogger(__name__)

APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

# APNs rejects provider tokens older than an hour and throttles ones that are
# refreshed more often than every 20 minutes.
PROVIDER_TOKEN_TTL_S = 50 * 60

_provider_token_cache: tuple[str, float] | None = None


def _provider_token(settings: Settings, now: float | None = None) -> str:
    """ES256 JWT that authenticates this server to APNs, reused for 50 minutes."""
    global _provider_token_cache
    now = time.time() if now is None else now
    if _provider_token_cache is None or now - _provider_token_cache[1] >= PROVIDER_TOKEN_TTL_S:
        key = Path(settings.apns_key_path).read_text()
        token = jwt.encode(
            {"iss": settings.apns_team_id, "iat": int(now)},
            key,
            algorithm="ES256",
            headers={"kid": settings.apns_key_id},
        )
        _provider_token_cache = (token, now)
    return _provider_token_cache[0]


@dataclass(frozen=True)
class PushTarget:
    token: str
    apns_env: str


async def deliver(
    client: httpx.AsyncClient,
    targets: list[PushTarget],
    headers: dict[str, str],
    payload: dict[str, Any],
) -> tuple[int, list[str]]:
    """POST one notification to each target. Returns (sent, dead tokens)."""
    sent = 0
    dead: list[str] = []
    for t in targets:
        url = f"{APNS_HOSTS[t.apns_env]}/3/device/{t.token}"
        try:
            r = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError:
            log.exception("apns request failed")
            continue
        if r.status_code == 200:
            sent += 1
            continue
        try:
            reason = r.json().get("reason", "")
        except ValueError:
            reason = r.text
        # 410: the app was uninstalled. BadDeviceToken: a token from the other
        # APNs environment, or garbage. Either way it will never deliver.
        if r.status_code == 410 or reason == "BadDeviceToken":
            dead.append(t.token)
        else:
            log.warning("apns push rejected: %s %s", r.status_code, reason)
    return sent, dead


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(http2=True, timeout=10.0)


async def send_to_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> int:
    """Fan a single notification out to every device registered to `user_id`."""
    settings = get_settings()
    if not settings.apns_key_path:
        return 0
    rows = (
        await session.execute(
            select(Device.push_token, Device.apns_env).where(Device.user_id == user_id)
        )
    ).all()
    if not rows:
        return 0

    payload = {
        "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
        **(data or {}),
    }
    try:
        headers = {
            "authorization": f"bearer {_provider_token(settings)}",
            "apns-topic": settings.apns_topic,
            "apns-push-type": "alert",
        }
        async with _client() as client:
            sent, dead = await deliver(
                client, [PushTarget(tok, env) for tok, env in rows], headers, payload
            )
        if dead:
            await session.execute(delete(Device).where(Device.push_token.in_(dead)))
            await session.commit()
    except Exception:
        log.exception("apns push send failed")
        return 0
    return sent
