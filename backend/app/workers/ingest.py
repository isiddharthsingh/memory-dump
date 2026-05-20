from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete

from app.ai.analyze import analyze
from app.ai.embed import chunk_text, embed_texts
from app.db.models import Item, ItemChunk
from app.db.session import get_sessionmaker
from app.extractors.base import get_extractor

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
        log.exception("ingest failed for %s", item_id)
        async with Session() as session:
            item = await session.get(Item, item_uuid)
            if item is not None:
                item.status = "failed"
                item.error = f"{type(exc).__name__}: {exc}"[:1000]
                await session.commit()
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
