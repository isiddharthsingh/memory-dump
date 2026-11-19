"""Tokens spent on models and Voyage, and what they cost: per user, day, model
and whose key paid in `llm_usage` (the month's free allowance and a user's own
spend limit are sums over it, app/ai/routing.py), and in Prometheus without
the user. "platform" is the server's key, counted against the user's
allowance; "own" is the user's key; "exempt" is the server's key for work
that is the server's own, counted against nobody. The user comes from the log context: get_current_user binds it for
a request, the jobs bind it once they know whose item they hold."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app import pricing
from app.ai import routing
from app.db.models import LlmUsage
from app.db.session import get_sessionmaker
from app.metrics import PROVIDER_COST, PROVIDER_TOKENS

log = logging.getLogger(__name__)

_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


async def record_claude(
    purpose: str, model: str, usage: Any, *, provider: str = "anthropic", own: bool = False
) -> None:
    """A model call's tokens. `provider` and `own` say which key made it: the
    server's Anthropic key unless told otherwise."""
    tokens = {
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
    }
    # OpenRouter says what it billed.
    billed = getattr(usage, "cost", None)
    await _record(provider, purpose, model, tokens, own=own, billed=billed)


async def record_voyage(purpose: str, model: str, total_tokens: int | None) -> None:
    await _record("voyage", purpose, model, {"input_tokens": total_tokens or 0})


async def _record(
    provider: str,
    purpose: str,
    model: str,
    tokens: dict[str, int],
    *,
    own: bool = False,
    billed: float | None = None,
) -> None:
    # The server's own work on a user's rows (routing.EXEMPT_JOBS) is kept
    # apart: counted as theirs, it would use up their month's allowance.
    billed_to = "own" if own else "exempt" if routing.exempt() else "platform"
    for kind, n in tokens.items():
        if n:
            PROVIDER_TOKENS.labels(provider, model, purpose, kind.removesuffix("_tokens")).inc(n)
    cost = pricing.cost(provider, model, billed=billed, **tokens)
    PROVIDER_COST.labels(provider, billed_to).inc(float(cost))
    user_id = structlog.contextvars.get_contextvars().get("user_id")
    if user_id is None:
        return
    values = {k: tokens.get(k, 0) for k in _TOKENS}
    stmt = insert(LlmUsage).values(
        user_id=uuid.UUID(user_id),
        day=func.current_date(),
        model=model,
        billed_to=billed_to,
        calls=1,
        cost_usd=cost,
        **values,
    )
    summed = ("calls", "cost_usd", *_TOKENS)
    stmt = stmt.on_conflict_do_update(
        index_elements=[LlmUsage.user_id, LlmUsage.day, LlmUsage.model, LlmUsage.billed_to],
        set_={k: getattr(LlmUsage, k) + getattr(stmt.excluded, k) for k in summed},
    )
    try:
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()
    except Exception:
        # Losing a count beats failing the call that spent it.
        log.warning("couldn't record token usage", exc_info=True)
