from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegister(BaseModel):
    push_token: str = Field(pattern=r"^[0-9a-f]{64,200}$")
    apns_env: Literal["sandbox", "production"]
    platform: str | None = Field(default=None, max_length=16)


@router.post("/register")
async def register_device(
    body: DeviceRegister,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    # push_token is unique globally — same physical device can only
    # belong to one user at a time. On conflict (e.g. user A logged out and
    # user B logged in on the same phone) we transfer ownership.
    stmt = (
        insert(Device)
        .values(
            user_id=current_user.id,
            push_token=body.push_token,
            apns_env=body.apns_env,
            platform=body.platform,
        )
        .on_conflict_do_update(
            index_elements=["push_token"],
            set_={
                "user_id": current_user.id,
                "apns_env": body.apns_env,
                "platform": body.platform,
            },
        )
        .returning(Device.id)
    )
    row = (await session.execute(stmt)).scalar_one()
    await session.commit()
    return {"id": str(row)}


@router.delete("/{push_token}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    push_token: Annotated[str, Path(pattern=r"^[0-9a-f]{64,200}$")],
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Stops pushes to this phone for this account: the app calls it as it
    signs out, so the next person to use the phone isn't sent this account's
    titles. Answers 204 whether or not the token was registered."""
    await session.execute(
        delete(Device).where(Device.user_id == current_user.id, Device.push_token == push_token)
    )
    await session.commit()


@router.get("/count")
async def device_count(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Device.id).where(Device.user_id == current_user.id)
        )
    ).scalars().all()
    return {"count": len(rows)}
