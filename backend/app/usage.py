"""Tokens spent on Claude and Voyage: per user, day and model in `llm_usage`
(cost per user, and what a quota would count), and in Prometheus without the
user. The user comes from the log context: get_current_user binds it for a
request, the jobs bind it once they know whose item they hold."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.db.models import LlmUsage
from app.db.session import get_sessionmaker
from app.metrics import PROVIDER_TOKENS

log = logging.getLogger(__name__)

_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


async def record_claude(purpose: str, model: str, usage: Any) -> None:
    tokens = {
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
    }
    await _record("anthropic", purpose, model, tokens)


async def record_voyage(purpose: str, model: str, total_tokens: int | None) -> None:
    await _record("voyage", purpose, model, {"input_tokens": total_tokens or 0})


async def _record(provider: str, purpose: str, model: str, tokens: dict[str, int]) -> None:
    for kind, n in tokens.items():
        if n:
            PROVIDER_TOKENS.labels(provider, model, purpose, kind.removesuffix("_tokens")).inc(n)
    user_id = structlog.contextvars.get_contextvars().get("user_id")
    if user_id is None:
        return
    values = {k: tokens.get(k, 0) for k in _TOKENS}
    stmt = insert(LlmUsage).values(
        user_id=uuid.UUID(user_id), day=func.current_date(), model=model, calls=1, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[LlmUsage.user_id, LlmUsage.day, LlmUsage.model],
        set_={k: getattr(LlmUsage, k) + getattr(stmt.excluded, k) for k in ("calls", *_TOKENS)},
    )
    try:
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()
    except Exception:
        # Losing a count beats failing the call that spent it.
        log.warning("couldn't record token usage", exc_info=True)
