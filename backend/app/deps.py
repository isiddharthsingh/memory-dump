import secrets
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_access_token
from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_session


def require_token(
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> None:
    """Legacy single-user bearer-token guard.

    Kept while the per-router rewires to `get_current_user` happen one at
    a time. Will be removed once the last router is converted.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(presented, settings.api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


_BEARER_PREFIX = "Bearer "


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Decode the access JWT in the Authorization header, load the user.

    Returns 401 on any failure (missing header, expired/invalid token,
    user no longer exists). Use as a dependency on every owner-scoped
    route.
    """
    if authorization is None or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix(_BEARER_PREFIX).strip()
    try:
        claims = decode_access_token(settings, token)
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user = await session.get(User, claims.user_id)
    if user is None:
        # Token references a deleted account.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
