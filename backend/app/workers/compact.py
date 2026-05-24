"""Nightly compaction: merge near-duplicate topic pages.

Runs as an arq cron job. Asks Claude to inspect the slug list, propose
merges, and rewrites each merge target with content from its source(s),
then deletes the merged-away pages.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import delete, select

from app.ai.synthesize import (
    PATCH_SYSTEM,
    _strip_json_fence,
    embed_page_chunks,
)
from app.config import get_settings
from app.db.models import TopicPage, TopicPageChunk, User
from app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

MERGE_SYSTEM = """You inspect a list of topic slugs from a personal wiki \
and propose merges for slugs that clearly cover the same concept.

Return STRICT JSON:
{
  "merges": [
    {"keep": "canonical-slug", "absorb": ["dup-slug-1", "dup-slug-2"]},
    ...
  ]
}

Rules:
- Only merge near-duplicates (e.g. "rust-async" + "async-rust"). Be
  conservative — when in doubt, don't merge.
- "keep" is the slug to retain; "absorb" lists slugs whose content
  should be merged into "keep" and then deleted.
- Both "keep" and every "absorb" slug must exist in the input list.
"""


async def compact_topics(ctx: dict[str, Any]) -> str:
    """Per-user merge pass — runs the Claude prompt once per user with
    that user's slugs, then merges in-scope."""
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    Session = get_sessionmaker()

    total_merged = 0
    async with Session() as session:
        user_rows = (await session.execute(select(User.id))).scalars().all()

        for user_id in user_rows:
            rows = (
                await session.execute(
                    select(TopicPage.slug).where(TopicPage.user_id == user_id)
                )
            ).all()
            slugs = [s for (s,) in rows]
            if len(slugs) < 4:
                continue

            resp = await client.messages.create(
                model=settings.claude_analysis_model,
                max_tokens=512,
                system=[{"type": "text", "text": MERGE_SYSTEM}],
                messages=[
                    {"role": "user", "content": "Slugs:\n" + "\n".join(sorted(slugs))}
                ],
            )
            raw = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            )
            try:
                plan = json.loads(_strip_json_fence(raw))
            except json.JSONDecodeError:
                log.warning("compact: bad JSON for user %s: %s", user_id, raw[:200])
                continue

            merges = plan.get("merges") or []
            if not merges:
                continue

            for merge in merges:
                keep = merge.get("keep")
                absorb = merge.get("absorb") or []
                if not isinstance(keep, str) or not isinstance(absorb, list):
                    continue
                keep_page = (
                    await session.execute(
                        select(TopicPage)
                        .where(TopicPage.user_id == user_id)
                        .where(TopicPage.slug == keep)
                    )
                ).scalar_one_or_none()
                if keep_page is None:
                    continue

                for dup_slug in absorb:
                    if not isinstance(dup_slug, str) or dup_slug == keep:
                        continue
                    dup_page = (
                        await session.execute(
                            select(TopicPage)
                            .where(TopicPage.user_id == user_id)
                            .where(TopicPage.slug == dup_slug)
                        )
                    ).scalar_one_or_none()
                    if dup_page is None:
                        continue

                    # Naive merge: append dup body under a `## Merged from <slug>`
                    # heading. The next ingest into the kept page will let Claude
                    # re-organize it.
                    merged_body = (
                        keep_page.body_md.rstrip()
                        + f"\n\n## Merged from {dup_slug}\n\n"
                        + dup_page.body_md.strip()
                    )
                    # Strip wikilinks pointing back at the absorbed slug.
                    merged_body = re.sub(
                        rf"\[\[{re.escape(dup_slug)}\]\]", f"[[{keep}]]", merged_body
                    )

                    keep_page.body_md = merged_body
                    keep_page.version = (keep_page.version or 1) + 1
                    existing_ids = list(keep_page.source_item_ids or [])
                    for sid in dup_page.source_item_ids or []:
                        if sid not in existing_ids:
                            existing_ids.append(sid)
                    keep_page.source_item_ids = existing_ids

                    await session.delete(dup_page)
                    total_merged += 1

                # Re-embed kept page after all absorbs.
                await session.execute(
                    delete(TopicPageChunk).where(TopicPageChunk.topic_page_id == keep_page.id)
                )
                chunks_with_emb = await embed_page_chunks(keep_page.body_md)
                for i, (text, vec) in enumerate(chunks_with_emb):
                    session.add(
                        TopicPageChunk(
                            topic_page_id=keep_page.id,
                            user_id=user_id,
                            ord=i,
                            text=text,
                            embedding=vec,
                        )
                    )

        await session.commit()
        log.info("compact: merged %d page(s) across all users", total_merged)
        return f"merged:{total_merged}"


# Cron registration handled in worker.py.
_ = PATCH_SYSTEM
