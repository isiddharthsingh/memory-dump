import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ItemDetail,
    ItemPatch,
    ItemSummary,
    RelatedResponse,
    TopicPageSummary,
)
from app.db.models import Item, ItemChunk, TopicPage, User
from app.db.session import get_session
from app.deps import get_current_user
from app.queue import get_arq_pool

router = APIRouter(prefix="/items", tags=["items"])


async def _get_owned_item(
    session: AsyncSession, item_id: uuid.UUID, user: User
) -> Item:
    """Fetch an item or 404. 404 (not 403) on cross-user access so we
    don't leak which ids exist on the system."""
    item = await session.get(Item, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


@router.get("", response_model=list[ItemSummary])
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    tag: str | None = Query(None, description="Filter by tag (exact match)"),
    source: str | None = Query(
        None, description="Filter by source: youtube, twitter, instagram, reddit, web"
    ),
) -> list[Item]:
    stmt = select(Item).where(Item.user_id == current_user.id)
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
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, list[dict[str, int | str]]]:
    """Aggregate tag counts across the caller's ready items."""
    stmt = (
        select(func.unnest(Item.tags).label("tag"), func.count().label("n"))
        .where(Item.user_id == current_user.id)
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
    current_user: Annotated[User, Depends(get_current_user)],
) -> Item:
    return await _get_owned_item(session, item_id, current_user)


@router.patch("/{item_id}", response_model=ItemDetail)
async def patch_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Item:
    """Update mutable item fields (currently just `share_text`/note) and
    re-run the ingest pipeline so the new note is folded into embeddings.
    """
    item = await _get_owned_item(session, item_id, current_user)
    if body.share_text is not None:
        item.share_text = body.share_text or None
    item.status = "processing"
    item.error = None
    await session.commit()
    await session.refresh(item)
    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_item", str(item.id))
    return item


@router.post("/{item_id}/retry", response_model=ItemDetail)
async def retry_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Item:
    item = await _get_owned_item(session, item_id, current_user)
    # Uploaded files are deleted once processed (see workers/ingest.py), so
    # there's nothing to re-run; `ingest_item` would treat the `file://`
    # placeholder as a web page.
    if item.url.startswith("file://"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Uploaded files can't be retried. Share the file again.",
        )
    item.status = "pending"
    item.error = None
    await session.commit()
    await session.refresh(item)
    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_item", str(item.id))
    return item


@router.get("/{item_id}/related", response_model=RelatedResponse)
async def related_items(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(6, ge=1, le=20),
) -> RelatedResponse:
    """Wiki pages that cite this item + items closest to it by embedding."""
    item = await _get_owned_item(session, item_id, current_user)

    wiki_stmt = (
        select(TopicPage)
        .where(TopicPage.user_id == current_user.id)
        .where(TopicPage.source_item_ids.any(item.id))
        .order_by(TopicPage.updated_at.desc())
    )
    wiki_rows = (await session.execute(wiki_stmt)).scalars().all()

    centroid_subq = (
        select(func.avg(ItemChunk.embedding))
        .where(ItemChunk.item_id == item_id)
        .where(ItemChunk.embedding.is_not(None))
        .scalar_subquery()
    )
    # voyage-3-large cosine distance: ~0.0 identical, ~0.35 strongly related,
    # ~0.6+ basically unrelated. 0.45 is a permissive but meaningful floor.
    RELATED_MAX_DISTANCE = 0.45
    neighbor_stmt = (
        select(
            Item,
            func.min(ItemChunk.embedding.cosine_distance(centroid_subq)).label("d"),
        )
        .join(ItemChunk, ItemChunk.item_id == Item.id)
        .where(Item.user_id == current_user.id)
        .where(Item.id != item_id)
        .where(Item.status == "ready")
        .where(ItemChunk.embedding.is_not(None))
        .group_by(Item.id)
        .having(func.min(ItemChunk.embedding.cosine_distance(centroid_subq)) < RELATED_MAX_DISTANCE)
        .order_by("d")
        .limit(limit)
    )
    rows = (await session.execute(neighbor_stmt)).all()
    related_items = [row[0] for row in rows if row[1] is not None]

    return RelatedResponse(
        in_wiki=[TopicPageSummary.model_validate(tp) for tp in wiki_rows],
        related=[ItemSummary.model_validate(it) for it in related_items],
    )


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    item = await _get_owned_item(session, item_id, current_user)
    await session.delete(item)
    await session.commit()
