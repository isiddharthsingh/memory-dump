import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestRequest, IngestResponse, ItemSummary
from app.auth.rate_limit import rate_limit
from app.db.models import Item, User
from app.db.session import get_session
from app.deps import get_current_user
from app.extractors.base import detect_source
from app.queue import enqueue_ingest
from app.storage import get_storage

_ingest_rate_limit = rate_limit("ingest", "rate_limit_ingest_per_hour")

router = APIRouter(
    prefix="/ingest", tags=["ingest"], dependencies=[Depends(_ingest_rate_limit)]
)

# 25 MB cap. Anthropic accepts PDFs up to ~32 MB; we leave headroom for
# request overhead. Bigger uploads get a 413 instead of a worker failure.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_CHUNK = 1024 * 1024

# What we accept. Extensions are derived from the mime type for the stored
# file's name only; the worker reads the mime from the row.
_PDF_MIME = "application/pdf"
_IMAGE_MIMES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
}
_MIME_TO_EXT = {
    _PDF_MIME: "pdf",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
}


@router.post("", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    body: IngestRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IngestResponse:
    url = str(body.url)
    # A URL is saved once per user: sharing it again returns the saved item,
    # and retries it if it failed.
    item = await _saved(session, current_user.id, url)
    if item is None:
        item = Item(
            user_id=current_user.id,
            url=url,
            source=detect_source(url),
            status="pending",
            share_text=body.share_text,
        )
        session.add(item)
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent share saved it between the lookup and the insert.
            await session.rollback()
            item = await _saved(session, current_user.id, url)
            if item is None:
                raise
            return IngestResponse(item=ItemSummary.model_validate(item))
    elif item.status == "failed":
        # Conditional, so of two concurrent re-shares only one re-queues it.
        reset = await session.execute(
            update(Item)
            .where(Item.id == item.id, Item.status == "failed")
            .values(status="pending", error=None, ingest_generation=Item.ingest_generation + 1)
            .returning(Item.id)
        )
        requeue = reset.scalar_one_or_none() is not None
        await session.commit()
        if not requeue:
            await session.refresh(item)
            return IngestResponse(item=ItemSummary.model_validate(item))
    else:
        return IngestResponse(item=ItemSummary.model_validate(item))
    await session.refresh(item)

    # If this fails after the commit, the reconciler queues the job instead.
    await enqueue_ingest(item.id, item.ingest_generation)

    return IngestResponse(item=ItemSummary.model_validate(item))


async def _saved(session: AsyncSession, user_id: uuid.UUID, url: str) -> Item | None:
    result = await session.execute(select(Item).where(Item.user_id == user_id, Item.url == url))
    return result.scalar_one_or_none()


@router.post("/file", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_file(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
    share_text: Annotated[str | None, Form(max_length=20_000)] = None,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> IngestResponse:
    """Accept a PDF or image, queue it for vision-based analysis.

    The bytes go to upload storage (app/storage.py) in chunks, never whole in
    memory; the worker reads, analyzes, and deletes them. Long-term we only
    keep the extracted text + analysis. A client that sends the same
    Idempotency-Key again (a resend after a timeout) gets the item the first
    request saved instead of a second one.
    """
    if idempotency_key:
        existing = await _by_key(session, current_user.id, idempotency_key)
        if existing is not None:
            return IngestResponse(item=ItemSummary.model_validate(existing))

    mime = (file.content_type or "").lower()
    if mime != _PDF_MIME and mime not in _IMAGE_MIMES:
        raise HTTPException(status_code=415, detail=f"Unsupported content type: {mime!r}")
    if not file.size:
        raise HTTPException(status_code=400, detail="Empty file")
    if file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({file.size} bytes; max {MAX_UPLOAD_BYTES})",
        )

    source = "pdf" if mime == _PDF_MIME else "image"
    ext = _MIME_TO_EXT.get(mime, "bin")
    original_name = Path(file.filename or f"upload.{ext}").name
    # url is required and unique per user, so the `file://` placeholder
    # carries the item id: two different files can share a name. The title
    # holds the filename until analysis replaces it.
    item_id = uuid.uuid4()
    upload_key = f"{item_id}.{ext}"

    async def chunks() -> AsyncIterator[bytes]:
        while chunk := await file.read(_CHUNK):
            yield chunk

    # Stored before the row exists, so a queued item always has its file. A
    # row that never lands leaves a file the storage sweep deletes.
    await get_storage().save(upload_key, chunks())
    item = Item(
        id=item_id,
        user_id=current_user.id,
        url=f"file://{item_id}/{original_name}",
        source=source,
        status="pending",
        share_text=share_text,
        title=original_name,
        idempotency_key=idempotency_key or None,
        upload_key=upload_key,
        upload_mime=mime,
    )
    session.add(item)
    try:
        await session.commit()
    except IntegrityError:
        # The same key arrived twice at once; the other request saved it.
        await session.rollback()
        await get_storage().delete(upload_key)
        existing = await _by_key(session, current_user.id, idempotency_key or "")
        if existing is None:
            raise
        return IngestResponse(item=ItemSummary.model_validate(existing))
    await session.refresh(item)

    await enqueue_ingest(item.id, item.ingest_generation, upload=True)

    return IngestResponse(item=ItemSummary.model_validate(item))


async def _by_key(session: AsyncSession, user_id: uuid.UUID, key: str) -> Item | None:
    return await session.scalar(
        select(Item).where(Item.user_id == user_id, Item.idempotency_key == key)
    )
