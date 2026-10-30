"""The signed-in account: read it, or delete it.

Sign-up, sign-in, sign-out and refresh are SuperTokens' own routes under
/auth (see app/auth/supertokens.py); these two sit beside them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from supertokens_python.asyncio import delete_user, get_user

from app.db.models import User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime


@router.get("/me", response_model=UserOut)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    # The email lives in SuperTokens, which can change it from the dashboard.
    st_user = await get_user(str(current_user.id))
    return UserOut(
        id=current_user.id,
        email=st_user.emails[0] if st_user and st_user.emails else "",
        display_name=current_user.display_name,
        created_at=current_user.created_at,
    )


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
