import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy import DateTime, delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import kv, thumbnails
from app.api.cached import cached_json
from app.api.schemas import (
    ItemChanges,
    ItemDetail,
    ItemPatch,
    ItemSummary,
    RelatedResponse,
    TopicPageSummary,
)
from app.auth.rate_limit import rate_limit
from app.db.models import TOMBSTONE_DAYS, Item, ItemChunk, ItemTombstone, TopicPage, User
from app.db.session import get_session
from app.deps import get_current_user
from app.queue import enqueue, enqueue_ingest, forget_job_id
from app.storage import get_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["items"])

# Editing a note or retrying runs the whole pipeline again, so it counts
# against the same hourly cap as saving.
_ingest_rate_limit = rate_limit("ingest", "rate_limit_ingest_per_hour")

TAGS_TTL_S = 300
# /items/changes looks back this much further than `since`: a change made in a
# transaction that began before the last call but committed after it carries
# an earlier updated_at than that call's `until`.
CHANGES_OVERLAP = timedelta(seconds=60)


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
    before: Annotated[
        uuid.UUID | None,
        Query(
            description="Id of the last item of the previous page. Pages stay put "
            "while items are saved or deleted, which offset can't promise."
        ),
    ] = None,
    offset: int = Query(0, ge=0, description="Older paging; use `before`."),
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
        stmt = stmt.where(Item.tags.any(tag))  # type: ignore[arg-type]
    if before is not None:
        anchor = await _anchor(session, current_user.id, before)
        stmt = stmt.where(tuple_(Item.created_at, Item.id) < tuple_(*anchor))
    elif offset:
        stmt = stmt.offset(offset)
    stmt = stmt.order_by(Item.created_at.desc(), Item.id.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _anchor(
    session: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID
) -> tuple[datetime, uuid.UUID]:
    """Where a page starts: the cursor item's (created_at, id), from its
    tombstone when it was deleted since."""
    created = await session.scalar(
        select(Item.created_at).where(Item.id == item_id, Item.user_id == user_id)
    ) or await session.scalar(
        select(ItemTombstone.created_at).where(
            ItemTombstone.item_id == item_id, ItemTombstone.user_id == user_id
        )
    )
    if created is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown cursor")
    return created, item_id


@router.get("/tags", tags=["items"])
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, list[dict[str, int | str]]]:
    """Aggregate tag counts across the caller's ready items. Cached for
    TAGS_TTL_S, and dropped when an item is saved or deleted."""
    key = kv.tags_key(current_user.id)
    try:
        cached = await kv.redis().get(key)
    except RedisError:
        cached = None
    if cached:
        return {"tags": json.loads(cached)}
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
    tags: list[dict[str, int | str]] = [{"tag": r.tag, "count": r.n} for r in rows]
    with contextlib.suppress(RedisError):
        await kv.redis().set(key, json.dumps(tags), ex=TAGS_TTL_S)
    return {"tags": tags}


@router.get("/changes", response_model=ItemChanges)
async def item_changes(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    since: Annotated[datetime | None, Query(description="`until` from the previous call")] = None,
) -> ItemChanges:
    """Items saved or changed, and ids of items deleted, since `since`, so a
    client keeping an offline copy refreshes what changed instead of walking
    the whole library. Without `since`, or with one older than the tombstones
    kept (TOMBSTONE_DAYS), the answer is `full`: walk the list."""
    now = await session.scalar(select(func.now()))
    assert now is not None
    until = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if since is None or since < now - timedelta(days=TOMBSTONE_DAYS) + CHANGES_OVERLAP:
        return ItemChanges(full=True, changed=[], deleted=[], until=until)
    # The model maps items.updated_at as a naive timestamp, but the column is
    # timestamptz: bind `after` as one.
    after = literal(since - CHANGES_OVERLAP, DateTime(timezone=True))
    changed = await session.execute(
        select(Item.id).where(Item.user_id == current_user.id, Item.updated_at > after)
    )
    deleted = await session.execute(
        select(ItemTombstone.item_id).where(
            ItemTombstone.user_id == current_user.id, ItemTombstone.deleted_at > after
        )
    )
    return ItemChanges(
        full=False,
        changed=list(changed.scalars()),
        deleted=list(deleted.scalars()),
        until=until,
    )


@router.get("/{item_id}", response_model=ItemDetail)
async def get_item(
    item_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    item = await _get_owned_item(session, item_id, current_user)
    return cached_json(request, ItemDetail.model_validate(item))


@router.patch("/{item_id}", response_model=ItemDetail, dependencies=[Depends(_ingest_rate_limit)])
async def patch_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Item:
    """Update mutable item fields (currently just `share_text`/note) and
    re-run the ingest pipeline so the new note is folded into embeddings.
    A job still running for the old note writes nothing.
    """
    item = await _get_owned_item(session, item_id, current_user)
    values: dict[str, object] = {}
    if body.share_text is not None:
        values["share_text"] = body.share_text or None
    # An upload is analysed once: after that it has no upload_key, so there's
    # nothing to re-run (`ingest_item` would fetch the `file://` placeholder),
    # and the note is kept without being embedded.
    rerun = not (item.url.startswith("file://") and item.upload_key is None)
    if rerun:
        # "pending" until a worker claims it; the reconciler fails items left
        # "processing", and this one may wait in the queue a while.
        values.update(status="pending", error=None, ingest_generation=Item.ingest_generation + 1)
    if not values:
        return item
    await session.execute(update(Item).where(Item.id == item.id).values(**values))
    await session.commit()
    await session.refresh(item)
    if rerun:
        await enqueue_ingest(item.id, item.ingest_generation, upload=item.upload_key is not None)
    return item


@router.post(
    "/{item_id}/retry", response_model=ItemDetail, dependencies=[Depends(_ingest_rate_limit)]
)
async def retry_item(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Item:
    item = await _get_owned_item(session, item_id, current_user)
    # An upload is analysed once (see workers/ingest.py); `ingest_item` would
    # treat the `file://` placeholder as a web page.
    if item.url.startswith("file://"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Uploaded files can't be retried. Share the file again.",
        )
    await session.execute(
        update(Item)
        .where(Item.id == item.id)
        .values(status="pending", error=None, ingest_generation=Item.ingest_generation + 1)
    )
    await session.commit()
    await session.refresh(item)
    await enqueue_ingest(item.id, item.ingest_generation)
    return item


@router.get(
    "/{item_id}/thumbnail",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def item_thumbnail(
    item_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """An uploaded image's or PDF's thumbnail, the item's `thumbnail_url`.
    It never changes, so clients may keep it as long as they like."""
    item = await _get_owned_item(session, item_id, current_user)
    missing = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No thumbnail")
    if item.thumbnail_url != thumbnails.path(item.id):
        raise missing
    # Not holding a connection while storage answers.
    await session.commit()
    try:
        jpeg = await get_storage().read(thumbnails.key(item_id))
    except FileNotFoundError:
        raise missing from None
    return Response(
        jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


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
        .where(TopicPage.source_item_ids.any(item.id))  # type: ignore[arg-type]
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
    """Deletes the item, then takes it out of the wiki and Ask history in the
    background (see `forget_item`)."""
    item = await _get_owned_item(session, item_id, current_user)
    # Queued first, so a delete never lands without its cleanup to follow;
    # forget_item waits until the item is gone. One job per item, however
    # often the delete is sent.
    await enqueue("forget_item", str(current_user.id), str(item_id), _job_id=forget_job_id(item_id))
    # The chunks go by ON DELETE CASCADE, without loading their embeddings.
    # The tombstone tells other devices (GET /items/changes).
    files = [key for key in (item.upload_key, item.file_key) if key]
    if item.thumbnail_url == thumbnails.path(item.id):
        files.append(thumbnails.key(item.id))
    # Two deletes of one item can both get here; the second adds nothing.
    await session.execute(
        pg_insert(ItemTombstone)
        .values(item_id=item.id, user_id=item.user_id, created_at=item.created_at)
        .on_conflict_do_nothing(index_elements=[ItemTombstone.item_id])
    )
    await session.execute(delete(Item).where(Item.id == item.id))
    await session.commit()
    await kv.drop(kv.tags_key(current_user.id))
    for key in files:
        try:
            await get_storage().delete(key)
        except Exception:
            # The item is gone, so the reconciler's sweep deletes it.
            log.warning("couldn't delete %s; the storage sweep will", key, exc_info=True)
