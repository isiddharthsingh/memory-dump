"""Whose key a model call uses, and whether the user may still spend on it.

A user with a saved key of their own in use (`users.ai_provider`) has every
Claude-shaped call made with that key, on that provider's models, up to the
monthly limit they may have set. Everyone else is on the server's keys: free
up to FREE_MONTHLY_USD a calendar month (UTC), then refused until the month
ends or they add a key. Admins have no cap.

Voyage (embeddings, reranking) is the server's key too, unless the user saved
a Voyage key of their own, which then makes every Voyage call of theirs. On
the server's key its cost counts toward the free allowance, and a user on the
allowance is refused it once that is used up. A user on their own model key
can't embed with it, so they get VOYAGE_FREE_MONTHLY_USD of the server's
Voyage a month and are refused past that until they add a Voyage key.

The user comes from the log context, as in app/usage.py. A call with no user
(a script, a test) is the server's own and isn't capped; neither is one made
under an exempt job (`exempt`), which is the server's whoever's rows it
works on. Spend is read before each call and written after it, so calls in
flight together can overshoot a limit by what they cost.

Nothing of a user's goes to a model or to Voyage until they agree to it
(users.ai_consent_at, asked for by the app): every call made for them
raises ConsentRequired instead. The exempt jobs aren't asked: `reembed` and
`classify` skip those accounts themselves, and `forget_item` finishes a
deletion the user asked for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import ScalarSelect, and_, func, or_, select
from sqlalchemy.orm import aliased

from app import secretbox
from app.ai import catalog
from app.ai.catalog import Role
from app.ai.wire import ProviderError
from app.config import get_settings
from app.db.models import LlmUsage, ProviderKey, User
from app.db.session import get_sessionmaker
from app.errors import ConsentRequired, SpendLimitReached

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
PLATFORM_VOYAGE = Route(provider=catalog.VOYAGE.id)


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
    # The user's own Voyage key, and the month's Voyage on the server's.
    voyage_secret: str | None
    voyage_spent: Decimal
    # Agreed to their content going to the AI services (users.ai_consent_at).
    consented: bool = True

    @property
    def on_own_key(self) -> bool:
        return bool(self.provider and self.secret and secretbox.available())

    @property
    def on_own_voyage(self) -> bool:
        return bool(self.voyage_secret and secretbox.available())

    @property
    def voyage_limit(self) -> Decimal | None:
        """The month's cap on the server's Voyage, which only an account on
        its own model key has, and not with a Voyage key or the admin flag."""
        if self.is_admin or self.on_own_voyage or not self.on_own_key:
            return None
        limit = Decimal(str(get_settings().voyage_free_monthly_usd))
        return limit if limit > 0 else None


def free_limit() -> Decimal | None:
    """The month's free allowance, or None when the server sets no cap."""
    limit = Decimal(str(get_settings().free_monthly_usd))
    return limit if limit > 0 else None


def resets(today: date | None = None) -> date:
    """The day the month's spend starts again from zero."""
    today = today or datetime.now(UTC).date()
    return date(today.year + today.month // 12, today.month % 12 + 1, 1)


def _spent(billed_to: str, *, voyage: bool = False) -> ScalarSelect[Decimal]:
    spent = select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(
        LlmUsage.user_id == User.id,
        LlmUsage.billed_to == billed_to,
        LlmUsage.day >= func.date_trunc("month", func.current_date()),
    )
    if voyage:
        # `llm_usage` names the model, not whose it is; Voyage's are these.
        spent = spent.where(or_(LlmUsage.model.like("voyage-%"), LlmUsage.model.like("rerank-%")))
    return spent.correlate(User).scalar_subquery()


async def account(user_id: uuid.UUID) -> Account | None:
    voyage_key = aliased(ProviderKey)
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
            voyage_key.secret,
            _spent("platform", voyage=True),
            User.ai_consent_at.is_not(None),
        )
        .outerjoin(
            ProviderKey,
            and_(ProviderKey.user_id == User.id, ProviderKey.provider == User.ai_provider),
        )
        .outerjoin(
            voyage_key,
            and_(voyage_key.user_id == User.id, voyage_key.provider == catalog.VOYAGE.id),
        )
        .where(User.id == user_id)
    )
    async with get_sessionmaker()() as session:
        # More columns than SQLAlchemy types a row for.
        row: Any = (await session.execute(stmt)).one_or_none()
    return Account(*row) if row else None


def exempt() -> bool:
    """Whether the work under way is the server's own (EXEMPT_JOBS)."""
    return structlog.contextvars.get_contextvars().get("job") in EXEMPT_JOBS


async def _caller() -> Account | None:
    """The account a call is made for; None when it is the server's own.
    ConsentRequired when its user hasn't agreed to the AI services: nothing
    of theirs goes to a model or to Voyage."""
    user_id = structlog.contextvars.get_contextvars().get("user_id")
    if user_id is None or exempt():
        return None
    acct = await account(uuid.UUID(user_id))
    if acct is not None and not acct.consented:
        raise ConsentRequired()
    return acct


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
    return Route(
        provider=acct.provider,
        own=True,
        key=_own_key(acct, acct.provider, acct.secret),
        region=acct.region,
        models=catalog.chosen(acct.provider, acct.models),
        user_id=_user_id(),
    )


def _user_id() -> uuid.UUID:
    return uuid.UUID(structlog.contextvars.get_contextvars()["user_id"])


def _own_key(acct: Account, provider: str, secret: str) -> str:
    """The user's key for a call, or SpendLimitReached past the limit they
    set for their own keys."""
    if acct.own_limit is not None and acct.own_spent >= acct.own_limit:
        raise SpendLimitReached(own=True, limit=acct.own_limit, resets=resets())
    try:
        return secretbox.unseal(secret)
    except secretbox.Unreadable as exc:
        # As good as a key the provider turned down: the user saves it again.
        raise ProviderError(provider, 401, str(exc)) from exc


async def voyage() -> Route:
    """The route for a Voyage call, or SpendLimitReached: the user's own
    Voyage key when they saved one, else the server's, while their free
    allowance lasts or, on their own model key, their month's share of it."""
    acct = await _caller()
    if acct is None:
        return PLATFORM_VOYAGE
    if acct.on_own_voyage:
        assert acct.voyage_secret
        key = _own_key(acct, catalog.VOYAGE.id, acct.voyage_secret)
        return Route(provider=catalog.VOYAGE.id, own=True, key=key, user_id=_user_id())
    if not acct.on_own_key:
        _check_free(acct)
    elif (limit := acct.voyage_limit) is not None and acct.voyage_spent >= limit:
        raise SpendLimitReached(own=False, voyage=True, limit=limit, resets=resets())
    return PLATFORM_VOYAGE
