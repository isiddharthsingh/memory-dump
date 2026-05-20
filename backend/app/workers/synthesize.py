from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select

from app.ai.synthesize import embed_page_chunks, patch_page, propose_slugs
from app.db.models import Item, TopicPage, TopicPageChunk
from app.db.session import get_sessionmaker

log = logging.getLogger(__name__)


async def synthesize_topics(ctx: dict[str, Any], item_id: str) -> str:
    """Fold a freshly-ingested item into AI-maintained topic pages."""
    item_uuid = uuid.UUID(item_id)
    Session = get_sessionmaker()

    async with Session() as session:
        item = await session.get(Item, item_uuid)
        if item is None or item.status != "ready":
            log.info("synthesize: item %s not ready", item_id)
            return "skipped"

        existing_slugs_rows = await session.execute(select(TopicPage.slug))
        existing_slugs = [s for (s,) in existing_slugs_rows.all()]

        proposed = await propose_slugs(
            item_title=item.title or item.url,
            item_summary=item.summary,
            item_topics=item.topics or [],
            item_tags=item.tags or [],
            existing_slugs=existing_slugs,
        )

        if not proposed.slugs:
            log.info("synthesize: no slugs proposed for %s", item_id)
            return "no_slugs"

        updated: list[str] = []
        for slug in proposed.slugs:
            # Fetch (or create) the page.
            page = (
                await session.execute(select(TopicPage).where(TopicPage.slug == slug))
            ).scalar_one_or_none()
            existing_body = page.body_md if page else ""
            existing_title = page.title if page else None

            patched = await patch_page(
                slug=slug,
                existing_body=existing_body,
                existing_title=existing_title,
                item_id=item.id,
                item_title=item.title or item.url,
                item_summary=item.summary,
                item_text=item.raw_text,
                related_slugs=[s for s in existing_slugs if s != slug] + [s for s in proposed.slugs if s != slug],
            )

            chunks_with_emb = await embed_page_chunks(patched.body_md)

            if page is None:
                page = TopicPage(
                    slug=patched.slug,
                    title=patched.title,
                    body_md=patched.body_md,
                    backlinks=patched.backlinks or None,
                    source_item_ids=[item.id],
                    version=1,
                )
                session.add(page)
                await session.flush()
            else:
                page.title = patched.title
                page.body_md = patched.body_md
                page.backlinks = patched.backlinks or None
                page.version = (page.version or 1) + 1
                existing_ids = list(page.source_item_ids or [])
                if item.id not in existing_ids:
                    existing_ids.append(item.id)
                page.source_item_ids = existing_ids
                await session.execute(
                    delete(TopicPageChunk).where(TopicPageChunk.topic_page_id == page.id)
                )

            for i, (text, vec) in enumerate(chunks_with_emb):
                session.add(
                    TopicPageChunk(
                        topic_page_id=page.id,
                        ord=i,
                        text=text,
                        embedding=vec,
                    )
                )

            # Create stubs for any wikilink targets that don't exist yet,
            # so links resolve cleanly in the UI.
            for backlink_slug in patched.backlinks:
                exists = (
                    await session.execute(
                        select(TopicPage.id).where(TopicPage.slug == backlink_slug)
                    )
                ).first()
                if exists is None:
                    session.add(
                        TopicPage(
                            slug=backlink_slug,
                            title=backlink_slug.replace("-", " ").title(),
                            body_md="_pending_",
                            backlinks=None,
                            source_item_ids=None,
                            version=1,
                        )
                    )

            existing_slugs = list({*existing_slugs, slug})
            updated.append(slug)

        await session.commit()
        log.info("synthesize: patched %d page(s) for %s: %s", len(updated), item_id, updated)
        return f"ok:{','.join(updated)}"
