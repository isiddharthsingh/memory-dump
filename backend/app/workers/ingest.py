"""Ingest jobs: a saved link (`ingest_item`) or an uploaded file
(`ingest_file`), from extraction to embedded chunks, then wiki synthesis.

A job is queued for one generation of its item (Item.ingest_generation). It
claims the item only while that generation is current and the item is still
pending or processing, and saves under the item's row lock only if it still
is. So a job overtaken by an edited note or a retry writes nothing, and two
runs of one job never interleave their chunks.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update

from app import kv, storage, thumbnails
from app.ai.analyze import Analysis, analyze
from app.ai.embed import chunk_text, embed_texts
from app.ai.providers import get_vision_provider
from app.config import get_settings
from app.db.models import Item, ItemChunk
from app.db.session import get_sessionmaker
from app.errors import is_transient
from app.extractors.base import get_extractor
from app.notifications import send_to_user
from app.queue import enqueue, synthesize_job_id
from app.workers.jobs import job, last_try, later

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Claim:
    item_id: uuid.UUID
    user_id: uuid.UUID
    generation: int
    url: str
    source: str
    share_text: str | None
    title: str | None
    upload_key: str | None
    upload_mime: str | None


async def _claim(item_id: uuid.UUID, generation: int | None) -> _Claim | None:
    """Marks the item processing, unless it is gone, already settled, or a
    newer request moved its generation on. Jobs queued by an older API carry
    no generation and take the current one."""
    stmt = (
        update(Item)
        .where(Item.id == item_id, Item.status.in_(("pending", "processing")))
        .values(status="processing", updated_at=func.now())
        .returning(
            Item.user_id,
            Item.ingest_generation,
            Item.url,
            Item.source,
            Item.share_text,
            Item.title,
            Item.upload_key,
            Item.upload_mime,
        )
    )
    if generation is not None:
        stmt = stmt.where(Item.ingest_generation == generation)
    async with get_sessionmaker()() as session:
        row = (await session.execute(stmt)).one_or_none()
        await session.commit()
    if row is None:
        return None
    structlog.contextvars.bind_contextvars(item_id=str(item_id), user_id=str(row.user_id))
    return _Claim(
        item_id=item_id,
        user_id=row.user_id,
        generation=row.ingest_generation,
        url=row.url,
        source=row.source,
        share_text=row.share_text,
        title=row.title,
        upload_key=row.upload_key,
        upload_mime=row.upload_mime,
    )


def _text_for_embed(analysis: Analysis, raw_text: str | None) -> str:
    return "\n\n".join(
        part
        for part in [
            f"Title: {analysis.title}" if analysis.title else None,
            f"Summary: {analysis.summary}" if analysis.summary else None,
            "Tags: " + ", ".join(analysis.tags) if analysis.tags else None,
            "People: " + ", ".join(analysis.people) if analysis.people else None,
            "Handles: " + ", ".join(analysis.handles) if analysis.handles else None,
            "Brands: " + ", ".join(analysis.brands_products) if analysis.brands_products else None,
            raw_text,
        ]
        if part
    )


async def _save(
    claim: _Claim,
    *,
    title: str,
    analysis: Analysis,
    raw_text: str | None,
    thumbnail_url: str | None,
    chunks: list[str],
    embeddings: list[list[float]],
    file_key: str | None = None,
) -> bool:
    """Writes the result, unless the claim's generation is no longer current."""
    async with get_sessionmaker()() as session:
        item = (
            await session.execute(select(Item).where(Item.id == claim.item_id).with_for_update())
        ).scalar_one_or_none()
        if item is None or item.ingest_generation != claim.generation:
            return False
        item.title = title
        item.summary = analysis.summary
        item.raw_text = raw_text
        item.thumbnail_url = thumbnail_url
        item.tags = analysis.tags or None
        item.people = analysis.people or None
        item.handles = analysis.handles or None
        item.brands_products = analysis.brands_products or None
        item.topics = analysis.topics or None
        item.status = "ready"
        item.error = None
        item.upload_key = None
        item.file_key = file_key
        item.wiki_status = "pending"

        # Replace any previous chunks (idempotent re-ingest).
        await session.execute(delete(ItemChunk).where(ItemChunk.item_id == claim.item_id))
        session.add_all(
            ItemChunk(item_id=claim.item_id, user_id=claim.user_id, ord=i, text=text, embedding=vec)
            for i, (text, vec) in enumerate(zip(chunks, embeddings, strict=True))
        )
        await session.commit()
    await kv.drop(kv.tags_key(claim.user_id))
    return True


async def _set_status(claim: _Claim, status: str, error: str) -> bool:
    """Sets the status while the claim's generation is current."""
    async with get_sessionmaker()() as session:
        updated = await session.scalar(
            update(Item)
            .where(Item.id == claim.item_id, Item.ingest_generation == claim.generation)
            .values(status=status, error=error[:1000])
            .returning(Item.id)
        )
        await session.commit()
    return updated is not None


async def _failed(ctx: dict[str, Any], claim: _Claim, exc: Exception) -> str:
    """Retries a transient failure later (raising Retry); marks the item
    failed on anything else, or on the last try. "superseded" when a newer
    generation owns the item by now: that job carries on, this one stops."""
    if is_transient(exc) and not last_try(ctx, exc):
        log.warning("ingest: transient error on try %s: %r", ctx.get("job_try", 1), exc)
        if not await _set_status(claim, "pending", f"retrying: {type(exc).__name__}"):
            return "superseded"
        raise later(ctx, 60, exc) from exc
    if is_transient(exc):
        log.error("ingest: gave up after %s tries: %r", ctx.get("job_try", 1), exc)
    else:
        log.exception("ingest failed")
    if not await _set_status(claim, "failed", f"{type(exc).__name__}: {exc}"):
        return "superseded"
    async with get_sessionmaker()() as session:
        await send_to_user(
            session,
            claim.user_id,
            title="Save failed",
            body=(claim.title or claim.url)[:120] + f" — {type(exc).__name__}",
            data={"item_id": str(claim.item_id)},
        )
    return "failed"


async def _synthesize_next(ctx: dict[str, Any], claim: _Claim) -> None:
    redis = ctx.get("redis")
    if redis is not None:
        await enqueue(
            "synthesize_topics",
            str(claim.item_id),
            _job_id=synthesize_job_id(claim.item_id, claim.generation),
            pool=redis,
        )


@job
async def ingest_item(ctx: dict[str, Any], item_id: str, generation: int | None = None) -> str:
    """Full ingest pipeline for a single Item.

    1. Claim it (processing).
    2. Extract content via per-source extractor, within EXTRACT_TIMEOUT_S.
    3. Analyze with Claude → title, summary, tags, lexical anchors.
    4. Chunk + embed combined text with Voyage.
    5. Persist; mark ready. Transient failures retry, others mark failed.
    6. Enqueue wiki-synthesis follow-up.
    """
    claim = await _claim(uuid.UUID(item_id), generation)
    if claim is None:
        log.info("ingest: item %s is gone, settled or superseded", item_id)
        return "skipped"

    try:
        extractor = get_extractor(claim.source)
        async with asyncio.timeout(get_settings().extract_timeout_s):
            content = await extractor.extract(claim.url, share_text=claim.share_text)
        analysis = await analyze(content, url=claim.url, source=claim.source)
        chunks = chunk_text(_text_for_embed(analysis, content.raw_text))
        embeddings = await embed_texts(chunks, input_type="document") if chunks else []
    except Exception as exc:
        return await _failed(ctx, claim, exc)

    saved = await _save(
        claim,
        title=analysis.title or content.title or claim.url,
        analysis=analysis,
        raw_text=content.raw_text,
        thumbnail_url=content.thumbnail_url,
        chunks=chunks,
        embeddings=embeddings,
    )
    if not saved:
        return "superseded"
    await _synthesize_next(ctx, claim)
    return "ready"


@job
async def ingest_file(
    ctx: dict[str, Any],
    item_id: str,
    file_path: str | None = None,
    mime_type: str | None = None,
    generation: int | None = None,
) -> str:
    """Vision-based ingest for an uploaded PDF or image.

    Mirrors `ingest_item` (claim, retries, wiki kickoff) but bypasses the URL
    extractor: the vision provider returns both the analysis and the extracted
    text in one call. A thumbnail goes beside the file (app/thumbnails.py).
    Once the item is saved the file is kept as its `file_key`; once it has
    failed for good, both are deleted. `file_path` and `mime_type` come from
    jobs an older API queued; newer ones find the upload on the item.
    """
    claim = await _claim(uuid.UUID(item_id), generation)
    if claim is None:
        log.info("ingest_file: item %s is gone, settled or superseded", item_id)
        if file_path:
            await storage.legacy_delete(file_path)
        return "skipped"
    original_name = claim.title or "upload"
    mime = claim.upload_mime or mime_type or ""

    try:
        if claim.upload_key:
            data = await storage.get_storage().read(claim.upload_key)
        elif file_path:
            data = await storage.legacy_read(file_path)
        else:
            raise FileNotFoundError(f"upload missing for {item_id}")

        provider = get_vision_provider()
        if mime == "application/pdf":
            result = await provider.analyze_pdf(data, filename=original_name)
        else:
            result = await provider.analyze_image(data, mime_type=mime)
        analysis = result.analysis
        raw_text = result.raw_text or ""
        chunks = chunk_text(_text_for_embed(analysis, raw_text))
        embeddings = await embed_texts(chunks, input_type="document") if chunks else []
        thumbnail_url = await _save_thumbnail(claim, data, mime)
    except Exception as exc:
        outcome = await _failed(ctx, claim, exc)
        # A superseded job leaves the file to the newer one.
        if outcome == "failed":
            await _discard_upload(claim, file_path)
        return outcome

    saved = await _save(
        claim,
        title=analysis.title or original_name,
        analysis=analysis,
        raw_text=raw_text or None,
        thumbnail_url=thumbnail_url,
        chunks=chunks,
        embeddings=embeddings,
        file_key=claim.upload_key,
    )
    if not saved:
        # The newer job for this item reads the same upload.
        return "superseded"
    if file_path:
        # An older API's upload lives outside storage, so it isn't kept.
        await _discard(storage.legacy_delete(file_path))
    await _synthesize_next(ctx, claim)
    return "ready"


async def _save_thumbnail(claim: _Claim, data: bytes, mime: str) -> str | None:
    """Stores the file's thumbnail and returns its URL, or None when the
    file can't be drawn."""
    jpeg = await asyncio.to_thread(thumbnails.make, data, mime)
    if jpeg is None:
        return None

    async def one() -> AsyncIterator[bytes]:
        yield jpeg

    await storage.get_storage().save(thumbnails.key(claim.item_id), one())
    return thumbnails.path(claim.item_id)


async def _discard_upload(claim: _Claim, file_path: str | None) -> None:
    """Deletes the files of an upload that failed for good."""
    store = storage.get_storage()
    if claim.upload_key:
        await _discard(store.delete(claim.upload_key))
    await _discard(store.delete(thumbnails.key(claim.item_id)))
    if file_path:
        await _discard(storage.legacy_delete(file_path))


async def _discard(deletion: Awaitable[None]) -> None:
    try:
        await deletion
    except Exception:
        log.warning("couldn't delete an upload; the storage sweep will", exc_info=True)
