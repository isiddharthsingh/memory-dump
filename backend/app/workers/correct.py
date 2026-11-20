"""Rewrites a wiki page after its reader corrected it.

`POST /wiki/{slug}/corrections` keeps what the user said the page gets wrong
on the page (`topic_pages.corrections`) and queues `correct_page`. The job has
Claude rewrite the page to agree with every correction it carries, drafting
from a copy outside any transaction and writing under the wiki lock only if
the page hasn't changed since, like every other writer of pages. From then on
synthesis patches the page with the corrections in front of it
(app/ai/synthesize.py `patch_page`), so a later save can't bring back what the
user corrected.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog

from app.ai import routing
from app.ai import synthesize as ai
from app.ai.synthesize import WIKILINK_RE
from app.db.models import TopicPage
from app.db.session import get_sessionmaker
from app.workers.jobs import job
from app.workers.synthesize import (
    MAX_ROUNDS,
    WikiBusy,
    correction_texts,
    lock_wiki,
    replace_chunks,
    retry_or_give_up,
)

log = logging.getLogger(__name__)


@job
async def correct_page(ctx: dict[str, Any], user_id: str, page_id: str) -> str:
    """Rewrites the page to agree with its corrections. Safe to run again:
    it reads the page and its corrections afresh each time. When it gives up
    the page keeps its text, and the corrections still bind the next patch."""
    structlog.contextvars.bind_contextvars(user_id=user_id, page_id=page_id)
    try:
        for _ in range(MAX_ROUNDS):
            outcome = await _correct(uuid.UUID(user_id), uuid.UUID(page_id))
            if outcome is not None:
                return outcome
        raise WikiBusy(f"page {page_id} kept changing")
    except Exception as exc:
        retry_or_give_up(ctx, exc, f"correct {page_id}")
        return "failed"


async def _correct(user_id: uuid.UUID, page_id: uuid.UUID) -> str | None:
    """None when the page changed under the draft, to draft it again."""
    Session = get_sessionmaker()
    async with Session() as session:
        page = await session.get(TopicPage, page_id)
        if page is None or page.user_id != user_id:
            return "gone"
        corrections = correction_texts(page)
        # A stub has nothing written yet; its first patch reads the corrections.
        if not corrections or not page.source_item_ids:
            return "nothing"
        version, slug, title, body = page.version, page.slug, page.title, page.body_md

    # The rewrite is embedded last: not paid for when Voyage would refuse it.
    await routing.voyage()
    rewritten = await ai.correct_page(slug=slug, title=title, body=body, corrections=corrections)
    if rewritten is None:
        log.warning("correct: no rewrite of %s; its corrections wait for the next patch", slug)
        return "kept"
    if rewritten == body:
        return "unchanged"
    chunks = await ai.embed_page_chunks(rewritten, title)

    async with Session() as session:
        await lock_wiki(session, user_id)
        page = await session.get(TopicPage, page_id)
        if page is None:
            return "gone"
        if page.version != version:
            return None
        page.body_md = rewritten
        page.backlinks = sorted(set(WIKILINK_RE.findall(rewritten)) - {slug}) or None
        page.version = version + 1
        await replace_chunks(session, page, chunks)
        await session.commit()
    log.info("correct: rewrote %s for %d correction(s)", slug, len(corrections))
    return "rewritten"
