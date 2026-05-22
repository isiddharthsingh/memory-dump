from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestRequest, IngestResponse, ItemSummary
from app.config import get_settings
from app.db.models import Item
from app.db.session import get_session
from app.deps import require_token
from app.extractors.base import detect_source
from app.queue import get_arq_pool

router = APIRouter(prefix="/ingest", tags=["ingest"], dependencies=[Depends(require_token)])

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
) -> IngestResponse:
    url = str(body.url)
    item = Item(
        url=url,
        source=detect_source(url),
        status="pending",
        share_text=body.share_text,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_item", str(item.id))

    return IngestResponse(item=ItemSummary.model_validate(item))


@router.post("/file", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_file(
    session: Annotated[AsyncSession, Depends(get_session)],
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
    # url is required + serves as a human-readable handle. We use a
    # `file://` placeholder containing the original filename so the inbox
    # has something meaningful to show before analysis finishes.
    placeholder_url = f"file://{original_name}"

    item = Item(
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
