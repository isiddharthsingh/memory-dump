import uuid
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from supertokens_python.recipe.session import SessionContainer
from supertokens_python.recipe.session.framework.fastapi import verify_session

from app.db.models import User
from app.db.session import get_session


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[SessionContainer, Depends(verify_session())],
) -> User:
    """Verify the SuperTokens access token, load the user.

    SuperTokens answers 401 itself for a missing, invalid or expired token,
    which tells the app to refresh. A valid token for an account that has
    since been deleted is a 401 too. Use as a dependency on every owner-scoped
    route.
    """
    try:
        user_id = uuid.UUID(auth.get_user_id())
    except ValueError:
        user_id = None
    user = await session.get(User, user_id) if user_id else None
    # Ends the read, so the request holds no pooled connection while it waits
    # on Claude or Voyage; its next query opens a new transaction.
    await session.commit()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Logs and token usage from here on carry the user.
    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user
