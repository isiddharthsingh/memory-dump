from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from arq import Retry
from sqlalchemy import Text, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.synthesize import (
    WIKILINK_RE,
    embed_page_chunks,
    forget_in_body,
    patch_page,
    propose_slugs,
    remove_from_page,
)
from app.db.models import Chat, Item, TopicPage, TopicPageChunk
from app.db.session import get_sessionmaker
from app.workers.ingest import TRANSIENT, _is_anthropic_overloaded

log = logging.getLogger(__name__)

# `[item:<id>]` and `[wiki:<slug>]` in an Ask answer, as the app reads them.
_ANSWER_MARKER_RE = re.compile(r"\s*\[(item|wiki):([^\]]+)\]")


def _later(ctx: dict[str, Any], first: int) -> Retry:
    """Runs the job again after `first` seconds, doubling per try (cap 30m)."""
    return Retry(defer=min(first * 2 ** (ctx.get("job_try", 1) - 1), 30 * 60))


async def _lock_wiki(session: AsyncSession, user_id: uuid.UUID, ctx: dict[str, Any]) -> None:
    """Holds the user's wiki until the transaction ends, so synthesis and
    forget_item never write a page from a copy the other has since changed.
    Waits up to 30 s, then runs the job again later, so jobs queued behind a
    long synthesis neither hold a worker slot nor run into job_timeout."""
    await session.execute(text("SET LOCAL lock_timeout = '30s'"))
    try:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"wiki:{user_id}"))))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":  # lock_not_available
            raise
        raise _later(ctx, 15) from exc
    await session.execute(text("SET LOCAL lock_timeout TO DEFAULT"))


async def _replace_chunks(
    session: AsyncSession, page: TopicPage, chunks_with_emb: list[tuple[str, list[float]]]
) -> None:
    """Swaps a page's search chunks for freshly embedded ones."""
    await session.execute(delete(TopicPageChunk).where(TopicPageChunk.topic_page_id == page.id))
    for i, (chunk, vec) in enumerate(chunks_with_emb):
        session.add(
            TopicPageChunk(
                topic_page_id=page.id, user_id=page.user_id, ord=i, text=chunk, embedding=vec
            )
        )


async def synthesize_topics(ctx: dict[str, Any], item_id: str) -> str:
    """Fold a freshly-ingested item into AI-maintained topic pages.

    All page reads/writes are scoped to `item.user_id` so wiki synthesis
    only ever touches the owner's pages.
    """
    item_uuid = uuid.UUID(item_id)
    Session = get_sessionmaker()

    async with Session() as session:
        item = await session.get(Item, item_uuid)
        if item is None or item.status != "ready":
            log.info("synthesize: item %s not ready", item_id)
            return "skipped"

        user_id = item.user_id
        await _lock_wiki(session, user_id, ctx)

        existing_slugs_rows = await session.execute(
            select(TopicPage.slug).where(TopicPage.user_id == user_id)
        )
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
            # Fetch (or create) the page — scoped to this user.
            page = (
                await session.execute(
                    select(TopicPage)
                    .where(TopicPage.user_id == user_id)
                    .where(TopicPage.slug == slug)
                )
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
                    user_id=user_id,
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

            await _replace_chunks(session, page, chunks_with_emb)

            # Create stubs for any wikilink targets that don't exist yet,
            # so links resolve cleanly in the UI. Skip slugs we're about to
            # patch as main pages in this same call, and rely on ON CONFLICT
            # over (user_id, slug) to absorb cross-worker races and
            # within-session autoflush gaps.
            for backlink_slug in patched.backlinks:
                if backlink_slug in proposed.slugs or backlink_slug in existing_slugs:
                    continue
                stmt = (
                    pg_insert(TopicPage)
                    .values(
                        user_id=user_id,
                        slug=backlink_slug,
                        title=backlink_slug.replace("-", " ").title(),
                        body_md="_pending_",
                        backlinks=None,
                        source_item_ids=None,
                        version=1,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_topic_pages_user_slug"
                    )
                )
                await session.execute(stmt)

            existing_slugs = list({*existing_slugs, slug})
            updated.append(slug)

        # Deleted while Claude wrote? Then none of this is kept. A delete after
        # this check is cleaned up by forget_item, which waits for the lock.
        if await session.scalar(select(Item.id).where(Item.id == item_uuid)) is None:
            log.info("synthesize: item %s was deleted", item_id)
            return "deleted"

        await session.commit()
        log.info("synthesize: patched %d page(s) for %s: %s", len(updated), item_id, updated)
        return f"ok:{','.join(updated)}"


async def forget_item(ctx: dict[str, Any], user_id: str, item_id: str) -> str:
    """Removes a deleted item from its owner's wiki and Ask history.

    Pages only it supported go at once, with the source cards that cite them.
    Then Claude rewrites each page that cites it or links to a page that's
    gone, one page per transaction, and stub pages nothing links to any more
    are deleted. Safe to run again: every step looks at what is still there,
    so a retry picks up where the last try stopped.
    """
    try:
        return await _forget(ctx, uuid.UUID(user_id), uuid.UUID(item_id))
    except Exception as exc:
        if isinstance(exc, TRANSIENT) or _is_anthropic_overloaded(exc):
            log.warning("forget: transient error for %s, retrying: %s", item_id, exc)
            raise _later(ctx, 60) from exc
        raise


async def _forget(ctx: dict[str, Any], user_id: uuid.UUID, item_id: uuid.UUID) -> str:
    Session = get_sessionmaker()
    cite = f"[item:{item_id}]"

    async with Session() as session:
        # The API queues this before it deletes, so wait for the delete to land.
        if await session.get(Item, item_id) is not None:
            raise _later(ctx, 15)
        await _lock_wiki(session, user_id, ctx)
        removed = await _drop_pages_of(session, user_id, item_id)
        await _scrub_chats(session, user_id, items={str(item_id)}, slugs=set(removed))
        rows = (
            await session.execute(
                select(TopicPage.id, TopicPage.slug, TopicPage.body_md).where(
                    TopicPage.user_id == user_id
                )
            )
        ).all()
        alive = {slug for _, slug, _ in rows}
        todo = [id_ for id_, _, body in rows if _needs_forgetting(body, cite, alive) is not None]
        await session.commit()

    rewritten: list[str] = []
    for page_id in todo:
        async with Session() as session:
            await _lock_wiki(session, user_id, ctx)
            page = await session.get(TopicPage, page_id)
            if page is None:
                continue
            alive = set(
                (await session.execute(select(TopicPage.slug).where(TopicPage.user_id == user_id)))
                .scalars()
                .all()
            )
            gone = _needs_forgetting(page.body_md, cite, alive)
            if gone is None:
                continue
            rewrite = await remove_from_page(
                body=page.body_md, item_id=item_id, gone_slugs=sorted(gone)
            )
            body = forget_in_body(rewrite or page.body_md, item_id, gone)
            chunks_with_emb = await embed_page_chunks(body)

            page.body_md = body
            page.backlinks = sorted(set(WIKILINK_RE.findall(body)) - {page.slug}) or None
            page.version = (page.version or 1) + 1
            await _replace_chunks(session, page, chunks_with_emb)
            await session.commit()
            rewritten.append(page.slug)

    async with Session() as session:
        await _lock_wiki(session, user_id, ctx)
        pages = (
            (await session.execute(select(TopicPage).where(TopicPage.user_id == user_id)))
            .scalars()
            .all()
        )
        # Stubs (pages no item filled in) that no page links to any more.
        linked = {s for p in pages for s in WIKILINK_RE.findall(p.body_md) if s != p.slug}
        stubs = [p.slug for p in pages if not p.source_item_ids and p.slug not in linked]
        if stubs:
            await session.execute(
                delete(TopicPage)
                .where(TopicPage.user_id == user_id)
                .where(TopicPage.slug.in_(stubs))
            )
        await session.commit()

    log.info(
        "forget: item %s removed %s, rewrote %s, dropped stubs %s",
        item_id,
        removed,
        rewritten,
        stubs,
    )
    return f"removed:{len(removed)},rewrote:{len(rewritten)},stubs:{len(stubs)}"


def _needs_forgetting(body: str, cite: str, alive: set[str]) -> set[str] | None:
    """The links to deleted pages in a page that has those or cites the item,
    or None when there's nothing on it to forget."""
    gone = set(WIKILINK_RE.findall(body)) - alive
    return gone if gone or cite in body.lower() else None


async def _drop_pages_of(
    session: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID
) -> list[str]:
    """Deletes the pages only this item supported and takes it off the other
    pages' sources. Returns the deleted slugs."""
    query = (
        select(TopicPage)
        .where(TopicPage.user_id == user_id)
        .where(TopicPage.source_item_ids.any_() == item_id)
    )
    pages = (await session.execute(query)).scalars().all()
    only = [p for p in pages if set(p.source_item_ids or []) == {item_id}]
    for page in pages:
        if page not in only:
            page.source_item_ids = [s for s in page.source_item_ids or [] if s != item_id]
    if only:
        await session.execute(delete(TopicPage).where(TopicPage.id.in_([p.id for p in only])))
    return [p.slug for p in only]


async def _scrub_chats(
    session: AsyncSession, user_id: uuid.UUID, items: set[str], slugs: set[str]
) -> None:
    """Takes deleted items and wiki pages out of saved Ask answers: their
    source cards and their `[item:…]` / `[wiki:…]` markers. A chat keeps its
    place in History, since `updated_at` doesn't change."""
    turns_text = Chat.turns.cast(Text)
    mentions = [turns_text.contains(ref, autoescape=True) for ref in items | slugs]
    query = select(Chat).where(Chat.user_id == user_id).where(or_(*mentions)).with_for_update()
    chats = (await session.execute(query)).scalars().all()

    def marker(m: re.Match[str]) -> str:
        kind, ref = m.group(1), m.group(2).strip()
        gone = ref.lower() in items if kind == "item" else ref in slugs
        return "" if gone else m.group(0)

    def kept(citation: dict[str, Any]) -> bool:
        if citation.get("kind") == "item":
            return str(citation.get("id")).lower() not in items
        return citation.get("slug") not in slugs

    for chat in chats:
        turns = [
            {
                **turn,
                "answer": _ANSWER_MARKER_RE.sub(marker, turn.get("answer") or ""),
                "citations": [c for c in turn.get("citations") or [] if kept(c)],
            }
            for turn in chat.turns
        ]
        if turns != chat.turns:
            chat.turns = turns
