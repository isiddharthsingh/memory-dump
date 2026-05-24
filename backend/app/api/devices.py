from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegister(BaseModel):
    expo_push_token: str
    platform: str | None = None


@router.post("/register")
async def register_device(
    body: DeviceRegister,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    # expo_push_token is unique globally — same physical device can only
    # belong to one user at a time. On conflict (e.g. user A logged out and
    # user B logged in on the same phone) we transfer ownership.
    stmt = (
        insert(Device)
        .values(
            user_id=current_user.id,
            expo_push_token=body.expo_push_token,
            platform=body.platform,
        )
        .on_conflict_do_update(
            index_elements=["expo_push_token"],
            set_={"user_id": current_user.id, "platform": body.platform},
        )
        .returning(Device.id)
    )
    row = (await session.execute(stmt)).scalar_one()
    await session.commit()
    return {"id": str(row)}


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
