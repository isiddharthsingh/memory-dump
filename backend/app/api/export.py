import asyncio
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from datetime import datetime, tzinfo
from typing import IO, Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.ask import zone
from app.db.models import Item, TopicPage, User
from app.db.session import get_session
from app.deps import get_current_user
from app.export import item_note, page_note

router = APIRouter(prefix="/export", tags=["export"])

# Items read per query, so a large library is never all in memory at once.
BATCH = 200
# The archive is kept in memory up to this size, then on disk.
SPOOL_BYTES = 8 * 1024 * 1024


def _add(vault: zipfile.ZipFile, notes: list[tuple[str, str]]) -> None:
    for path, note in notes:
        vault.writestr(path, note)


async def _write(
    archive: IO[bytes], session: AsyncSession, user_id: uuid.UUID, days: tzinfo
) -> None:
    """Writes the user's notes into `archive` as a zip. Each batch is
    compressed on a worker thread, so a large library doesn't stall the other
    requests this process is serving."""
    titles: dict[uuid.UUID, str | None] = {}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as vault:
        last = None
        while True:
            stmt = (
                select(Item)
                .where(Item.user_id == user_id, Item.status == "ready")
                .order_by(Item.created_at.desc(), Item.id.desc())
                .limit(BATCH)
            )
            if last is not None:
                stmt = stmt.where(tuple_(Item.created_at, Item.id) < tuple_(*last))
            items = (await session.execute(stmt)).scalars().all()
            titles.update((item.id, item.title) for item in items)
            await asyncio.to_thread(_add, vault, [item_note(item, days) for item in items])
            if len(items) < BATCH:
                break
            last = (items[-1].created_at, items[-1].id)
            # The rows' text is in the archive now; let go of it.
            session.expunge_all()

        pages = await session.execute(
            select(TopicPage)
            .where(TopicPage.user_id == user_id)
            # Stubs: pages a link named that no item has filled in yet.
            .where(TopicPage.source_item_ids.is_not(None))
            .order_by(TopicPage.slug)
        )
        notes = [page_note(page, titles, days) for page in pages.scalars()]
        await asyncio.to_thread(_add, vault, notes)


def _send(archive: IO[bytes]) -> Iterator[bytes]:
    with archive:
        while chunk := archive.read(64 * 1024):
            yield chunk


@router.get(
    "/obsidian",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/zip": {}}}},
)
async def export_obsidian(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    tz: Annotated[
        str | None, Query(max_length=64, description="IANA time zone the notes' dates are in")
    ] = None,
) -> StreamingResponse:
    """The caller's library as a zip to unpack into an Obsidian vault: a
    note per ready item under `Items/` and per wiki page under `Wiki/`
    (app/export.py). Text only: uploaded files and thumbnails stay on the
    server."""
    days = zone(tz)
    archive = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115
    try:
        await _write(archive, session, current_user.id, days)
        # Nothing more to read: no connection is held while the archive is sent.
        await session.commit()
    except BaseException:
        archive.close()
        raise
    archive.seek(0)
    name = f"memory-dump-{datetime.now(days).date().isoformat()}.zip"
    return StreamingResponse(
        _send(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
