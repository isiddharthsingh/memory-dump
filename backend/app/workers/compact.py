"""Nightly wiki upkeep, one job per user.

The `compact_topics` cron only queues a `compact_user` job for each user, so
no job walks every user and one user's failure is nobody else's. Each one:

1. Queues `forget_item` again for items the user's pages still cite that no
   longer exist: the job queued when each was deleted was lost or gave up.
2. Asks Claude to inspect the slug list and propose merges of near-duplicate
   pages. A merge is put together outside any transaction and written under
   the wiki lock only if none of its pages changed meanwhile; otherwise it
   waits for the next night.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text

from app.ai.clients import claude
from app.ai.synthesize import WIKILINK_RE, _strip_json_fence, embed_page_chunks
from app.config import get_settings
from app.db.models import TopicPage
from app.db.session import get_sessionmaker
from app.queue import enqueue, forget_job_id
from app.workers.jobs import job, user_ids
from app.workers.synthesize import lock_wiki, read_pages, replace_chunks, retry_or_give_up

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


@job
async def compact_topics(ctx: dict[str, Any]) -> str:
    """Queues compact_user for every user, once per user per day."""
    day = datetime.now(UTC).date().isoformat()
    queued = 0
    async for user_id in user_ids():
        await enqueue(
            "compact_user", str(user_id), _job_id=f"compact:{user_id}:{day}", pool=ctx.get("redis")
        )
        queued += 1
    log.info("compact: queued %d user(s)", queued)
    return f"queued:{queued}"


@job
async def compact_user(ctx: dict[str, Any], user_id: str) -> str:
    uid = uuid.UUID(user_id)
    structlog.contextvars.bind_contextvars(user_id=user_id)
    try:
        forgets = await _requeue_forgets(ctx, uid)
        merged = await _merge_duplicates(uid)
    except Exception as exc:
        retry_or_give_up(ctx, exc, f"compact {user_id}")
        return "failed"
    return f"forgets:{forgets},merged:{merged}"


async def _requeue_forgets(ctx: dict[str, Any], user_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT s.id
                FROM topic_pages tp, unnest(tp.source_item_ids) AS s(id)
                WHERE tp.user_id = :uid
                  AND NOT EXISTS (SELECT 1 FROM items i WHERE i.id = s.id)
                """
            ),
            {"uid": user_id},
        )
        gone = list(rows.scalars())
    for item_id in gone:
        await enqueue(
            "forget_item",
            str(user_id),
            str(item_id),
            _job_id=forget_job_id(item_id),
            pool=ctx.get("redis"),
        )
    if gone:
        log.info("compact: queued forget_item again for %d deleted item(s)", len(gone))
    return len(gone)


async def _merge_duplicates(user_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as session:
        slugs = sorted(
            (
                await session.execute(select(TopicPage.slug).where(TopicPage.user_id == user_id))
            ).scalars()
        )
    if len(slugs) < 4:
        return 0

    resp = await claude(
        purpose="compact",
        model=get_settings().claude_analysis_model,
        max_tokens=512,
        system=[{"type": "text", "text": MERGE_SYSTEM}],
        messages=[{"role": "user", "content": "Slugs:\n" + "\n".join(slugs)}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    try:
        plan = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        log.warning("compact: bad JSON: %s", raw[:200])
        return 0

    merged = 0
    for merge in plan.get("merges") or []:
        keep = merge.get("keep")
        absorb = merge.get("absorb") or []
        if not isinstance(keep, str) or not isinstance(absorb, list):
            continue
        merged += await _merge(
            user_id, keep, [s for s in absorb if isinstance(s, str) and s != keep]
        )
    log.info("compact: merged %d page(s)", merged)
    return merged


async def _merge(user_id: uuid.UUID, keep: str, absorb: list[str]) -> int:
    pages = await read_pages(user_id, [keep, *absorb])
    if keep not in pages:
        return 0
    dups = [pages[s] for s in absorb if s in pages]
    if not dups:
        return 0

    # Naive merge: append each dup body under a `## Merged from <slug>`
    # heading, pointing its links at the kept page. The next ingest into the
    # kept page lets Claude re-organize it.
    body = pages[keep].body_md
    for dup in dups:
        body = body.rstrip() + f"\n\n## Merged from {dup.slug}\n\n" + dup.body_md.strip()
        body = re.sub(rf"\[\[{re.escape(dup.slug)}\]\]", f"[[{keep}]]", body)
    chunks = await embed_page_chunks(body)
    versions = {p.slug: p.version for p in (pages[keep], *dups)}

    async with get_sessionmaker()() as session:
        await lock_wiki(session, user_id)
        current = {
            p.slug: p
            for p in (
                await session.execute(
                    select(TopicPage).where(
                        TopicPage.user_id == user_id, TopicPage.slug.in_(list(versions))
                    )
                )
            ).scalars()
        }
        if {slug: p.version for slug, p in current.items()} != versions:
            log.info("compact: %s changed meanwhile, merging it another night", keep)
            return 0
        kept = current[keep]
        kept.body_md = body
        kept.backlinks = sorted(set(WIKILINK_RE.findall(body)) - {keep}) or None
        kept.version = (kept.version or 1) + 1
        source_ids = list(kept.source_item_ids or [])
        for dup in dups:
            for sid in current[dup.slug].source_item_ids or []:
                if sid not in source_ids:
                    source_ids.append(sid)
            await session.delete(current[dup.slug])
        kept.source_item_ids = source_ids
        await replace_chunks(session, kept, chunks)
        await session.commit()
    return len(dups)
