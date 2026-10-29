import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestRequest, IngestResponse, ItemSummary
from app.auth.rate_limit import rate_limit
from app.config import get_settings
from app.db.models import Item, User
from app.db.session import get_session
from app.deps import get_current_user
from app.extractors.base import detect_source
from app.queue import get_arq_pool

_ingest_rate_limit = rate_limit("ingest", "rate_limit_ingest_per_hour")

router = APIRouter(
    prefix="/ingest", tags=["ingest"], dependencies=[Depends(_ingest_rate_limit)]
)

# 25 MB cap. Anthropic accepts PDFs up to ~32 MB; we leave headroom for
# request overhead. Bigger uploads get a 413 instead of a worker failure.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# What we accept. Extensions are derived from the mime type for the on-disk
# filename only; the worker re-reads the mime from the row.
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
            .values(status="pending", error=None)
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

    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_item", str(item.id))

    return IngestResponse(item=ItemSummary.model_validate(item))


async def _saved(session: AsyncSession, user_id: uuid.UUID, url: str) -> Item | None:
    result = await session.execute(select(Item).where(Item.user_id == user_id, Item.url == url))
    return result.scalar_one_or_none()


@router.post("/file", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_file(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
    share_text: Annotated[str | None, Form()] = None,
) -> IngestResponse:
    """Accept a PDF or image, queue it for vision-based analysis.

    The bytes land in a shared uploads volume; the worker reads, analyzes,
    and deletes them. Long-term we only keep the extracted text + analysis.
    """
    mime = (file.content_type or "").lower()
    if mime != _PDF_MIME and mime not in _IMAGE_MIMES:
        raise HTTPException(status_code=415, detail=f"Unsupported content type: {mime!r}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(data)} bytes; max {MAX_UPLOAD_BYTES})",
        )

    source = "pdf" if mime == _PDF_MIME else "image"
    ext = _MIME_TO_EXT.get(mime, "bin")
    original_name = Path(file.filename or f"upload.{ext}").name
    # url is required and unique per user, so the `file://` placeholder
    # carries the item id: two different files can share a name. The title
    # holds the filename until analysis replaces it.
    item_id = uuid.uuid4()
    placeholder_url = f"file://{item_id}/{original_name}"

    item = Item(
        id=item_id,
        user_id=current_user.id,
        url=placeholder_url,
        source=source,
        status="pending",
        share_text=share_text,
        title=original_name,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    uploads_dir = Path(get_settings().uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    on_disk = uploads_dir / f"{item.id}.{ext}"
    on_disk.write_bytes(data)

    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_file", str(item.id), str(on_disk), mime)

    return IngestResponse(item=ItemSummary.model_validate(item))
