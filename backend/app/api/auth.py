"""The signed-in account: read it, edit its name and phone, give or withdraw
consent to the AI services, or delete it.

Sign-up, sign-in, sign-out and refresh are SuperTokens' own routes under
/auth (see app/auth/supertokens.py); these sit beside them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from supertokens_python.asyncio import delete_user, get_user

from app.auth.supertokens import BAD_PHONE, MAX_FIELD_LEN, phone_number
from app.db.models import User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    phone: str | None
    created_at: datetime
    # When the user agreed to their content going to the AI services; null
    # until they do (or after they withdraw), and nothing is sent meanwhile.
    ai_consent_at: datetime | None


class ProfileIn(BaseModel):
    """What to change; a field left out or null stays as it is. An empty
    phone removes it: it's optional, so it can be taken back."""

    name: str | None = None
    phone: str | None = None


async def _profile(user: User) -> UserOut:
    # The email lives in SuperTokens, which can change it from the dashboard.
    st_user = await get_user(str(user.id))
    return UserOut(
        id=user.id,
        email=st_user.emails[0] if st_user and st_user.emails else "",
        display_name=user.display_name,
        phone=user.phone,
        created_at=user.created_at,
        ai_consent_at=user.ai_consent_at,
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return await _profile(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: ProfileIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserOut:
    """Changes the name, the phone number or both, held to sign-up's rules. A
    name can be replaced, not removed; an empty phone removes the number. The
    email is SuperTokens'."""
    name = body.name.strip() if body.name is not None else None
    if name is not None and not 0 < len(name) <= MAX_FIELD_LEN:
        raise _refuse(f"Name must be 1 to {MAX_FIELD_LEN} characters")
    values: dict[str, str | None] = {}
    if name is not None:
        values["display_name"] = name
    if body.phone is not None and not body.phone.strip():
        values["phone"] = None
    elif body.phone is not None:
        phone = phone_number(body.phone)
        if phone is None:
            raise _refuse(BAD_PHONE)
        values["phone"] = phone
    if values:
        # Written as given, not as a difference from the row read when the
        # request began: another device may have changed it since.
        await session.execute(update(User).where(User.id == current_user.id).values(**values))
        await session.commit()
        for field, value in values.items():
            setattr(current_user, field, value)
    return await _profile(current_user)


@router.put("/me/ai-consent", response_model=UserOut)
async def give_ai_consent(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserOut:
    """The user agrees to their content going to the AI services the app
    named. Kept from the first time: agreeing again changes nothing."""
    consented = (
        await session.execute(
            update(User)
            .where(User.id == current_user.id)
            .values(ai_consent_at=func.coalesce(User.ai_consent_at, func.now()))
            .returning(User.ai_consent_at)
        )
    ).scalar_one()
    await session.commit()
    current_user.ai_consent_at = consented
    return await _profile(current_user)


@router.delete("/me/ai-consent", response_model=UserOut)
async def withdraw_ai_consent(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserOut:
    """From now on nothing of the user's goes to an AI service: saves fail
    with ConsentRequired, Ask answers 403, and Search uses keywords only,
    until they agree again."""
    await session.execute(update(User).where(User.id == current_user.id).values(ai_consent_at=None))
    await session.commit()
    current_user.ai_consent_at = None
    return await _profile(current_user)


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Hard-delete the authenticated user.

    The ON DELETE CASCADE FKs on items, item_chunks, topic_pages,
    topic_page_chunks, devices, and chats clear every trace of the account in
    one statement; SuperTokens then drops the login and every session. If that
    call fails the row deletion rolls back. The uploaded files and thumbnails
    go with the next hourly storage sweep (`sweep_files`), their items gone.
    No tombstone — bring your own backup.
    """
    # A big library cascades through a lot of rows: more than the usual
    # statement timeout allows.
    await session.execute(text("SET LOCAL statement_timeout = '5min'"))
    await session.delete(current_user)
    await session.flush()
    await delete_user(str(current_user.id))
    await session.commit()
