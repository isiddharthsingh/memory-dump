from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device
from app.db.session import get_session
from app.deps import require_token

router = APIRouter(prefix="/devices", tags=["devices"], dependencies=[Depends(require_token)])


class DeviceRegister(BaseModel):
    expo_push_token: str
    platform: str | None = None


@router.post("/register")
async def register_device(
    body: DeviceRegister,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    stmt = (
        insert(Device)
        .values(expo_push_token=body.expo_push_token, platform=body.platform)
        .on_conflict_do_update(
            index_elements=["expo_push_token"],
            set_={"platform": body.platform},
        )
        .returning(Device.id)
    )
    row = (await session.execute(stmt)).scalar_one()
    await session.commit()
    return {"id": str(row)}


@router.get("/count")
async def device_count(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    rows = (await session.execute(select(Device.id))).scalars().all()
    return {"count": len(rows)}
