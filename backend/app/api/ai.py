"""Whose models the account uses, and what it has spent on them this month.

A user starts on the server's keys, free up to FREE_MONTHLY_USD a month. Here
they save a key of their own for a provider (checked with that provider
first, sealed before it is stored, never returned), choose which saved key is
in use and which of its models do each job, and set a monthly limit on what
their own keys may spend. A Voyage key is saved the same way but beside the
provider's, never in its place: it makes their embedding and reranking calls,
which otherwise stay on the server's key. Every route answers with the whole
of it, so the app redraws from one shape. Routing and the limits themselves
are in app/ai/routing.py.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import secretbox
from app.ai import catalog, clients, routing
from app.ai.catalog import ROLES
from app.ai.wire import ProviderError
from app.auth.rate_limit import rate_limit
from app.db.models import ProviderKey, User
from app.db.session import get_session
from app.deps import get_current_user
from app.errors import is_transient, retry_after

router = APIRouter(prefix="/ai", tags=["ai"])

# Saving a key calls the provider to check it.
_keys_rate_limit = rate_limit("keys", "rate_limit_keys_per_hour")

# What the app is shown of a saved key.
HINT_CHARS = 4


class KeyOut(BaseModel):
    hint: str
    region: str | None
    added: datetime


class ModelOut(BaseModel):
    id: str
    name: str


class ProviderOut(BaseModel):
    id: str
    name: str
    placeholder: str
    help: str
    # Whether its key comes with an AWS region.
    region: bool
    key: KeyOut | None
    models: list[ModelOut]
    # The model in use for each job: analysis, synthesis, ask.
    chosen: dict[str, str]


class FreeOut(BaseModel):
    # None when this account has no cap: an admin, or a server without one.
    limit: float | None
    spent: float
    resets: date


class OwnOut(BaseModel):
    limit: float | None
    spent: float


class VoyageOut(BaseModel):
    # What the `/ai/keys` routes call it.
    id: str
    name: str
    placeholder: str
    help: str
    # The user's own Voyage key, which makes their Voyage calls when saved.
    key: KeyOut | None
    # What the account may use of the server's Voyage key this month, when
    # it is capped apart from the free allowance (on its own model key, with
    # no Voyage key), and what it has used.
    limit: float | None
    spent: float


class AISettingsOut(BaseModel):
    admin: bool
    # False when the server can't keep keys (no KEYS_SECRET).
    keys: bool
    # The provider whose saved key is in use; None for the server's models.
    provider: str | None
    free: FreeOut
    own: OwnOut
    voyage: VoyageOut
    providers: list[ProviderOut]


class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=4096)
    region: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$")


class ProviderIn(BaseModel):
    provider: str | None


class LimitIn(BaseModel):
    limit: float | None = Field(gt=0, le=100_000)


class ModelsIn(BaseModel):
    analysis: str
    synthesis: str
    ask: str


def _provider(provider: str) -> catalog.Provider:
    known = catalog.PROVIDERS.get(provider)
    if known is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")
    return known


def _keyed(provider: str) -> catalog.Provider:
    """Whose key it is: a model provider's, or Voyage's."""
    return catalog.VOYAGE if provider == catalog.VOYAGE.id else _provider(provider)


def _key_out(key: ProviderKey | None) -> KeyOut | None:
    return KeyOut(hint=key.hint, region=key.region, added=key.created_at) if key else None


def _refuse(detail: str) -> HTTPException:
    """A 422 whose detail is a sentence the app shows as it is."""
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _settings(session: AsyncSession, user: User) -> AISettingsOut:
    saved = {
        key.provider: key
        for key in await session.scalars(select(ProviderKey).where(ProviderKey.user_id == user.id))
    }
    # Ends the read, like get_current_user: nothing here holds a connection.
    await session.commit()
    acct = await routing.account(user.id)
    if acct is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )
    limit = None if acct.is_admin else routing.free_limit()
    voyage_limit = acct.voyage_limit
    return AISettingsOut(
        admin=acct.is_admin,
        keys=secretbox.available(),
        provider=acct.provider if acct.on_own_key else None,
        free=FreeOut(
            limit=None if limit is None else float(limit),
            spent=round(float(acct.platform_spent), 4),
            resets=routing.resets(),
        ),
        own=OwnOut(
            limit=None if acct.own_limit is None else float(acct.own_limit),
            spent=round(float(acct.own_spent), 4),
        ),
        voyage=VoyageOut(
            id=catalog.VOYAGE.id,
            name=catalog.VOYAGE.name,
            placeholder=catalog.VOYAGE.placeholder,
            help=catalog.VOYAGE.help,
            key=_key_out(saved.get(catalog.VOYAGE.id)),
            limit=None if voyage_limit is None else float(voyage_limit),
            spent=round(float(acct.voyage_spent), 4),
        ),
        providers=[
            ProviderOut(
                id=p.id,
                name=p.name,
                placeholder=p.placeholder,
                help=p.help,
                region=p.region,
                key=_key_out(saved.get(p.id)),
                models=[ModelOut(id=m.id, name=m.name) for m in p.models],
                chosen={
                    str(role): model for role, model in catalog.chosen(p.id, acct.models).items()
                },
            )
            for p in catalog.PROVIDERS.values()
        ],
    )


@router.get("", response_model=AISettingsOut)
async def read_settings(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AISettingsOut:
    return await _settings(session, current_user)


@router.put(
    "/keys/{provider}", response_model=AISettingsOut, dependencies=[Depends(_keys_rate_limit)]
)
async def save_key(
    provider: str,
    body: KeyIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AISettingsOut:
    """Saves the user's key for a provider, once the provider has accepted
    it, and puts it in use. A Voyage key is in use while it is saved."""
    known = _keyed(provider)
    if not secretbox.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This server isn't set up to keep your own keys.",
        )
    key = body.key.strip()
    # A key travels in a header: anything but printable ASCII (a curly quote
    # or an invisible space picked up with a paste) can't be sent at all.
    if len(key) < 8 or " " in key or not (key.isascii() and key.isprintable()):
        raise _refuse("That doesn't look like a key.")
    region = (body.region or catalog.DEFAULT_REGION) if known.region else None
    try:
        await clients.check_key(provider, key, region)
    except ProviderError as exc:
        if exc.status_code in (401, 403):
            raise _refuse(f"{known.name} didn't accept that key.") from exc
        if is_transient(exc):
            wait = math.ceil(retry_after(exc) or 30)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{known.name} isn't answering right now. Try again in a minute.",
                headers={"Retry-After": str(wait)},
            ) from exc
        hint = " Check the region, and that Claude is enabled in it." if known.region else ""
        raise _refuse(f"{known.name} couldn't check that key.{hint}") from exc

    values = {"secret": secretbox.seal(key), "hint": key[-HINT_CHARS:], "region": region}
    await session.execute(
        insert(ProviderKey)
        .values(user_id=current_user.id, provider=provider, **values)
        .on_conflict_do_update(
            index_elements=[ProviderKey.user_id, ProviderKey.provider],
            set_={**values, "created_at": func.now()},
        )
    )
    if known is not catalog.VOYAGE:
        await session.execute(
            update(User).where(User.id == current_user.id).values(ai_provider=provider)
        )
    await session.commit()
    return await _settings(session, current_user)


@router.delete("/keys/{provider}", response_model=AISettingsOut)
async def remove_key(
    provider: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AISettingsOut:
    """Forgets the key. If it was in use, the account is back on the
    server's models, or for a Voyage key on the server's Voyage."""
    _keyed(provider)
    await session.execute(
        delete(ProviderKey).where(
            ProviderKey.user_id == current_user.id, ProviderKey.provider == provider
        )
    )
    await session.execute(
        update(User)
        .where(User.id == current_user.id, User.ai_provider == provider)
        .values(ai_provider=None)
    )
    await session.commit()
    return await _settings(session, current_user)


@router.put("/provider", response_model=AISettingsOut)
async def use_provider(
    body: ProviderIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AISettingsOut:
    """Which saved key is in use; null for the server's models."""
    if body.provider is not None:
        known = _provider(body.provider)
        saved = await session.get(ProviderKey, (current_user.id, body.provider))
        if saved is None:
            raise _refuse(f"Save a key for {known.name} first.")
    await session.execute(
        update(User).where(User.id == current_user.id).values(ai_provider=body.provider)
    )
    await session.commit()
    return await _settings(session, current_user)


@router.put("/limit", response_model=AISettingsOut)
async def set_limit(
    body: LimitIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AISettingsOut:
    """The most a month's calls on the user's own keys may cost; null for
    no limit."""
    limit = None if body.limit is None else Decimal(str(body.limit)).quantize(Decimal("0.01"))
    if limit is not None and limit <= 0:
        raise _refuse("The limit must be at least $0.01.")
    await session.execute(
        update(User).where(User.id == current_user.id).values(own_spend_limit_usd=limit)
    )
    await session.commit()
    return await _settings(session, current_user)


@router.put("/models/{provider}", response_model=AISettingsOut)
async def set_models(
    provider: str,
    body: ModelsIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AISettingsOut:
    """The provider's model for each job."""
    known = _provider(provider)
    offered = {m.id for m in known.models}
    picked = {role: getattr(body, role) for role in ROLES}
    if not offered.issuperset(picked.values()):
        raise _refuse(f"{known.name} doesn't offer that model here.")
    # Under the row lock: two devices picking for two providers both keep theirs.
    models = await session.scalar(
        select(User.ai_models).where(User.id == current_user.id).with_for_update()
    )
    await session.execute(
        update(User)
        .where(User.id == current_user.id)
        .values(ai_models={**(models or {}), provider: picked})
    )
    await session.commit()
    return await _settings(session, current_user)
