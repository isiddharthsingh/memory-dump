import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ItemDetail, ItemSummary
from app.db.models import Item
from app.db.session import get_session
from app.deps import require_token
from app.queue import get_arq_pool

router = APIRouter(prefix="/items", tags=["items"], dependencies=[Depends(require_token)])


@router.get("", response_model=list[ItemSummary])
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    tag: str | None = Query(None, description="Filter by tag (exact match)"),
    source: str | None = Query(
        None, description="Filter by source: youtube, twitter, instagram, web"
    ),
) -> list[Item]:
    stmt = select(Item)
    if status_filter:
        stmt = stmt.where(Item.status == status_filter)
    if source:
        stmt = stmt.where(Item.source == source)
    if tag:
        stmt = stmt.where(Item.tags.any(tag))
    stmt = stmt.order_by(Item.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/tags", tags=["items"])
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[dict[str, int | str]]]:
    """Aggregate tag counts across ready items."""
    stmt = (
        select(func.unnest(Item.tags).label("tag"), func.count().label("n"))
        .where(Item.status == "ready")
        .where(Item.tags.is_not(None))
        .group_by("tag")
        .order_by(func.count().desc())
        .limit(200)
    )
    rows = (await session.execute(stmt)).all()
    return {"tags": [{"tag": r.tag, "count": r.n} for r in rows]}


@router.get("/{item_id}", response_model=ItemDetail)
async def get_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Item:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.post("/{item_id}/retry", response_model=ItemDetail)
async def retry_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Item:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    item.status = "pending"
    item.error = None
    await session.commit()
    await session.refresh(item)
    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_item", str(item.id))
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    item = await session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    await session.delete(item)
    await session.commit()
