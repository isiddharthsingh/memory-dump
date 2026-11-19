"""Whose key a model call uses, and whether the user may still spend on it.

A user with a saved key of their own in use (`users.ai_provider`) has every
Claude-shaped call made with that key, on that provider's models, up to the
monthly limit they may have set. Everyone else is on the server's keys: free
up to FREE_MONTHLY_USD a calendar month (UTC), then refused until the month
ends or they add a key. Admins have no cap.

Voyage (embeddings, reranking) is always the server's. Its cost counts toward
the free allowance, and a user on the allowance is refused it once that is
used up; a user on their own key is not, since their own key can't embed.

The user comes from the log context, as in app/usage.py. A call with no user
(a script, a test) is the server's own and isn't capped; neither is one made
under an exempt job (`exempt`), which is the server's whoever's rows it
works on. Spend is read before each call and written after it, so calls in
flight together can overshoot a limit by what they cost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import structlog
from sqlalchemy import ScalarSelect, and_, func, select

from app import secretbox
from app.ai import catalog
from app.ai.catalog import Role
from app.ai.wire import ProviderError
from app.config import get_settings
from app.db.models import LlmUsage, ProviderKey, User
from app.db.session import get_sessionmaker
from app.errors import SpendLimitReached

# Work that is the server's to do and pay for, on its own keys and outside
# every cap, whoever the rows belong to: the operator's runs over every
# account (re-embedding, the kinds backfill), and taking a deleted item out
# of the wiki, which a used-up allowance must never hold back.
EXEMPT_JOBS = frozenset({"reembed", "classify", "forget_item"})


@dataclass(frozen=True)
class Route:
    """Where one call goes. The default is the server's Anthropic key."""

    provider: str = "anthropic"
    own: bool = False
    key: str | None = None
    region: str | None = None
    # The model each job uses, on an own key.
    models: dict[Role, str] | None = None
    user_id: uuid.UUID | None = None


PLATFORM = Route()


@dataclass(frozen=True)
class Account:
    """What decides a user's route, and what they've spent this month."""

    is_admin: bool
    # The provider in use, and its saved key (None when the key is gone).
    provider: str | None
    secret: str | None
    region: str | None
    models: dict[str, dict[str, str]] | None
    own_limit: Decimal | None
    platform_spent: Decimal
    own_spent: Decimal

    @property
    def on_own_key(self) -> bool:
        return bool(self.provider and self.secret and secretbox.available())


def free_limit() -> Decimal | None:
    """The month's free allowance, or None when the server sets no cap."""
    limit = Decimal(str(get_settings().free_monthly_usd))
    return limit if limit > 0 else None


def resets(today: date | None = None) -> date:
    """The day the month's spend starts again from zero."""
    today = today or datetime.now(UTC).date()
    return date(today.year + today.month // 12, today.month % 12 + 1, 1)


def _spent(billed_to: str) -> ScalarSelect[Decimal]:
    return (
        select(func.coalesce(func.sum(LlmUsage.cost_usd), 0))
        .where(
            LlmUsage.user_id == User.id,
            LlmUsage.billed_to == billed_to,
            LlmUsage.day >= func.date_trunc("month", func.current_date()),
        )
        .correlate(User)
        .scalar_subquery()
    )


async def account(user_id: uuid.UUID) -> Account | None:
    stmt = (
        select(
            User.is_admin,
            User.ai_provider,
            ProviderKey.secret,
            ProviderKey.region,
            User.ai_models,
            User.own_spend_limit_usd,
            _spent("platform"),
            _spent("own"),
        )
        .outerjoin(
            ProviderKey,
            and_(ProviderKey.user_id == User.id, ProviderKey.provider == User.ai_provider),
        )
        .where(User.id == user_id)
    )
    async with get_sessionmaker()() as session:
        row = (await session.execute(stmt)).one_or_none()
    return Account(*row) if row else None


def exempt() -> bool:
    """Whether the work under way is the server's own (EXEMPT_JOBS)."""
    return structlog.contextvars.get_contextvars().get("job") in EXEMPT_JOBS


async def _caller() -> Account | None:
    """The account a call is made for; None when it is the server's own."""
    user_id = structlog.contextvars.get_contextvars().get("user_id")
    if user_id is None or exempt():
        return None
    return await account(uuid.UUID(user_id))


def _check_free(acct: Account) -> None:
    limit = free_limit()
    if limit is not None and not acct.is_admin and acct.platform_spent >= limit:
        raise SpendLimitReached(own=False, limit=limit, resets=resets())


async def resolve() -> Route:
    """The route for a call the user's own key can make, or SpendLimitReached."""
    acct = await _caller()
    if acct is None:
        return PLATFORM
    if not acct.on_own_key:
        _check_free(acct)
        return PLATFORM
    assert acct.provider and acct.secret
    if acct.own_limit is not None and acct.own_spent >= acct.own_limit:
        raise SpendLimitReached(own=True, limit=acct.own_limit, resets=resets())
    try:
        key = secretbox.unseal(acct.secret)
    except secretbox.Unreadable as exc:
        # As good as a key the provider turned down: the user saves it again.
        raise ProviderError(acct.provider, 401, str(exc)) from exc
    user_id = uuid.UUID(structlog.contextvars.get_contextvars()["user_id"])
    return Route(
        provider=acct.provider,
        own=True,
        key=key,
        region=acct.region,
        models=catalog.chosen(acct.provider, acct.models),
        user_id=user_id,
    )


async def check_voyage() -> None:
    """SpendLimitReached when the user is on the free allowance and has used
    it up; Voyage is the server's key whoever calls."""
    acct = await _caller()
    if acct is not None and not acct.on_own_key:
        _check_free(acct)
