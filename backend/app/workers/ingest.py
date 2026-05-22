from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from arq import Retry
from sqlalchemy import delete

from app.ai.analyze import analyze
from app.ai.embed import chunk_text, embed_texts
from app.ai.providers import get_vision_provider
from app.db.models import Item, ItemChunk
from app.db.session import get_sessionmaker
from app.extractors.base import get_extractor
from app.notifications import send_to_all

# Transient error types from upstream APIs that should be retried with backoff
# rather than marked failed. Imported lazily so the worker boots without them.
try:
    from voyageai.error import RateLimitError as VoyageRateLimit
    from voyageai.error import ServiceUnavailableError as VoyageUnavailable
except ImportError:  # pragma: no cover
    VoyageRateLimit = VoyageUnavailable = ()  # type: ignore[assignment,misc]

try:
    from anthropic import APIStatusError as AnthropicAPIError
    from anthropic import RateLimitError as AnthropicRateLimit
except ImportError:  # pragma: no cover
    AnthropicRateLimit = AnthropicAPIError = ()  # type: ignore[assignment,misc]


def _is_anthropic_overloaded(exc: Exception) -> bool:
    return isinstance(exc, AnthropicAPIError) and getattr(exc, "status_code", None) in (
        429,
        503,
        529,
    )


TRANSIENT = (VoyageRateLimit, VoyageUnavailable, AnthropicRateLimit)

log = logging.getLogger(__name__)


async def ingest_item(ctx: dict[str, Any], item_id: str) -> str:
    """Full ingest pipeline for a single Item.

    1. Mark processing.
    2. Extract content via per-source extractor.
    3. Analyze with Claude → title, summary, tags, lexical anchors.
    4. Chunk + embed combined text with Voyage.
    5. Persist; mark ready. On any failure, mark failed with error.
    6. Enqueue wiki-synthesis follow-up.
    """
    item_uuid = uuid.UUID(item_id)
    Session = get_sessionmaker()

    async with Session() as session:
        item = await session.get(Item, item_uuid)
        if item is None:
            log.warning("ingest: item %s not found", item_id)
            return "not_found"
        item.status = "processing"
        await session.commit()
        url = item.url
        source = item.source
        share_text = item.share_text

    try:
        extractor = get_extractor(source)
        content = await extractor.extract(url, share_text=share_text)
        analysis = await analyze(content, url=url, source=source)

        text_for_embed = "\n\n".join(
            part
            for part in [
                f"Title: {analysis.title}" if analysis.title else None,
                f"Summary: {analysis.summary}" if analysis.summary else None,
                "Tags: " + ", ".join(analysis.tags) if analysis.tags else None,
                "People: " + ", ".join(analysis.people) if analysis.people else None,
                "Handles: " + ", ".join(analysis.handles) if analysis.handles else None,
                "Brands: " + ", ".join(analysis.brands_products)
                if analysis.brands_products
                else None,
                content.raw_text,
            ]
            if part
        )
        chunks = chunk_text(text_for_embed)
        embeddings = await embed_texts(chunks, input_type="document") if chunks else []
    except Exception as exc:
        # Transient upstream rate-limits / outages: keep status=pending and
        # let arq retry with exponential backoff. job_try is 1-indexed.
        if isinstance(exc, TRANSIENT) or _is_anthropic_overloaded(exc):
            job_try = ctx.get("job_try", 1)
            delay = min(60 * 2 ** (job_try - 1), 30 * 60)  # 60s, 120s, ... cap 30m
            log.warning(
                "ingest transient error for %s (try %d, retry in %ds): %s",
                item_id,
                job_try,
                delay,
                exc,
            )
            async with Session() as session:
                item = await session.get(Item, item_uuid)
                if item is not None:
                    item.status = "pending"
                    item.error = f"retrying: {type(exc).__name__}"[:1000]
                    await session.commit()
            raise Retry(defer=delay) from exc

        log.exception("ingest failed for %s", item_id)
        async with Session() as session:
            item = await session.get(Item, item_uuid)
            if item is not None:
                item.status = "failed"
                item.error = f"{type(exc).__name__}: {exc}"[:1000]
                await session.commit()
                await send_to_all(
                    session,
                    title="Save failed",
                    body=(item.title or item.url or "An item")[:120]
                    + f" — {type(exc).__name__}",
                    data={"item_id": str(item.id)},
                )
        return "failed"

    async with Session() as session:
        item = await session.get(Item, item_uuid)
        if item is None:
            return "not_found"

        item.title = analysis.title or content.title or item.url
        item.summary = analysis.summary
        item.raw_text = content.raw_text
        item.thumbnail_url = content.thumbnail_url
        item.tags = analysis.tags or None
        item.people = analysis.people or None
        item.handles = analysis.handles or None
        item.brands_products = analysis.brands_products or None
        item.topics = analysis.topics or None
        item.status = "ready"
        item.error = None

        # Replace any previous chunks (idempotent re-ingest).
        await session.execute(delete(ItemChunk).where(ItemChunk.item_id == item_uuid))
        for i, (text, vec) in enumerate(zip(chunks, embeddings, strict=True)):
            session.add(
                ItemChunk(
                    item_id=item_uuid,
                    ord=i,
                    text=text,
                    embedding=vec,
                )
            )
        await session.commit()

    # Kick off wiki synthesis (Phase 5). Worker is the same arq Redis.
    redis = ctx.get("redis")
    if redis is not None:
        await redis.enqueue_job("synthesize_topics", item_id)

    return "ready"


async def ingest_file(
    ctx: dict[str, Any],
    item_id: str,
    file_path: str,
    mime_type: str,
) -> str:
    """Vision-based ingest for an uploaded PDF or image.

    Mirrors `ingest_item` (status transitions, retries, wiki kickoff) but
    bypasses the URL extractor: the vision provider returns both the
    analysis and the extracted text in one call. The raw file is deleted
    once persisted.
    """
    item_uuid = uuid.UUID(item_id)
    Session = get_sessionmaker()
    path = Path(file_path)

    async with Session() as session:
        item = await session.get(Item, item_uuid)
        if item is None:
            log.warning("ingest_file: item %s not found", item_id)
            path.unlink(missing_ok=True)
            return "not_found"
        item.status = "processing"
        await session.commit()
        original_name = item.title or path.name

    try:
        if not path.exists():
            raise FileNotFoundError(f"upload missing: {file_path}")
        data = path.read_bytes()

        provider = get_vision_provider()
        if mime_type == "application/pdf":
            result = await provider.analyze_pdf(data, filename=original_name)
        else:
            result = await provider.analyze_image(data, mime_type=mime_type)
        analysis = result.analysis
        raw_text = result.raw_text or ""

        text_for_embed = "\n\n".join(
            part
            for part in [
                f"Title: {analysis.title}" if analysis.title else None,
                f"Summary: {analysis.summary}" if analysis.summary else None,
                "Tags: " + ", ".join(analysis.tags) if analysis.tags else None,
                "People: " + ", ".join(analysis.people) if analysis.people else None,
                "Handles: " + ", ".join(analysis.handles) if analysis.handles else None,
                "Brands: " + ", ".join(analysis.brands_products)
                if analysis.brands_products
                else None,
                raw_text,
            ]
            if part
        )
        chunks = chunk_text(text_for_embed)
        embeddings = await embed_texts(chunks, input_type="document") if chunks else []
    except Exception as exc:
        if isinstance(exc, TRANSIENT) or _is_anthropic_overloaded(exc):
            job_try = ctx.get("job_try", 1)
            delay = min(60 * 2 ** (job_try - 1), 30 * 60)
            log.warning(
                "ingest_file transient error for %s (try %d, retry in %ds): %s",
                item_id,
                job_try,
                delay,
                exc,
            )
            async with Session() as session:
                item = await session.get(Item, item_uuid)
                if item is not None:
                    item.status = "pending"
                    item.error = f"retrying: {type(exc).__name__}"[:1000]
                    await session.commit()
            raise Retry(defer=delay) from exc

        log.exception("ingest_file failed for %s", item_id)
        async with Session() as session:
            item = await session.get(Item, item_uuid)
            if item is not None:
                item.status = "failed"
                item.error = f"{type(exc).__name__}: {exc}"[:1000]
                await session.commit()
                await send_to_all(
                    session,
                    title="Save failed",
                    body=(item.title or original_name)[:120] + f" — {type(exc).__name__}",
                    data={"item_id": str(item.id)},
                )
        path.unlink(missing_ok=True)
        return "failed"

    async with Session() as session:
        item = await session.get(Item, item_uuid)
        if item is None:
            path.unlink(missing_ok=True)
            return "not_found"

        item.title = analysis.title or original_name
        item.summary = analysis.summary
        item.raw_text = raw_text or None
        item.tags = analysis.tags or None
        item.people = analysis.people or None
        item.handles = analysis.handles or None
        item.brands_products = analysis.brands_products or None
        item.topics = analysis.topics or None
        item.status = "ready"
        item.error = None

        await session.execute(delete(ItemChunk).where(ItemChunk.item_id == item_uuid))
        for i, (text, vec) in enumerate(zip(chunks, embeddings, strict=True)):
            session.add(
                ItemChunk(
                    item_id=item_uuid,
                    ord=i,
                    text=text,
                    embedding=vec,
                )
            )
        await session.commit()

    # Discard the file — per design we keep only the extracted text.
    path.unlink(missing_ok=True)

    redis = ctx.get("redis")
    if redis is not None:
        await redis.enqueue_job("synthesize_topics", item_id)

    return "ready"
