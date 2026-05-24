"""Authentication endpoints: signup, login, refresh, logout, me."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import jwt as jwt_helper
from app.auth.passwords import (
    MIN_PASSWORD_LEN,
    WeakPasswordError,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.config import Settings, get_settings
from app.db.models import RefreshToken, User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LEN)
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token TTL in seconds
    user: UserOut


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def _issue_session(
    session: AsyncSession,
    settings: Settings,
    user: User,
    user_agent: str | None,
) -> TokenResponse:
    access = jwt_helper.create_access_token(settings, user.id)
    refresh_raw = jwt_helper.generate_refresh_token()
    refresh = RefreshToken(
        user_id=user.id,
        token_hash=jwt_helper.hash_refresh_token(refresh_raw),
        expires_at=jwt_helper.refresh_token_expiry(settings),
        user_agent=(user_agent or None),
    )
    session.add(refresh)
    await session.commit()
    return TokenResponse(
        access_token=access,
        refresh_token=refresh_raw,
        expires_in=settings.jwt_access_ttl_min * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> TokenResponse:
    if not settings.allow_signup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Signups are disabled"
        )
    try:
        ph = hash_password(body.password)
    except WeakPasswordError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    user = User(
        email=_normalize_email(body.email),
        password_hash=ph,
        display_name=body.display_name,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from e

    return await _issue_session(session, settings, user, user_agent)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> TokenResponse:
    email = _normalize_email(body.email)
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    # Constant-ish work on the missing-user path: verify against a throwaway
    # hash so timing doesn't trivially leak account existence.
    if user is None:
        verify_password(body.password, "$argon2id$v=19$m=65536,t=3,p=4$Zm9v$Zm9v")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    return await _issue_session(session, settings, user, user_agent)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_agent: Annotated[str | None, Header(alias="User-Agent")] = None,
) -> TokenResponse:
    """Rotate the refresh token. Reuse of a revoked token revokes every
    refresh for that user (probable theft)."""
    token_hash = jwt_helper.hash_refresh_token(body.refresh_token)
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    if row.revoked_at is not None:
        # Reuse detection — nuke every active refresh for this user.
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected; all sessions revoked",
        )
    if row.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired"
        )

    user = await session.get(User, row.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )

    row.revoked_at = datetime.now(UTC)
    return await _issue_session(session, settings, user, user_agent)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: LogoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Revoke the presented refresh token. Idempotent — unknown/expired
    tokens get 204 too so logout always 'succeeds' client-side."""
    token_hash = jwt_helper.hash_refresh_token(body.refresh_token)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()


@router.get("/me", response_model=UserOut)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user
