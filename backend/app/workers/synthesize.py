from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import Text, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.synthesize import (
    WIKILINK_RE,
    PatchedPage,
    embed_page_chunks,
    forget_in_body,
    patch_page,
    propose_slugs,
    remove_from_page,
)
from app.db.models import Chat, Item, TopicPage, TopicPageChunk
from app.db.session import get_sessionmaker
from app.errors import is_transient
from app.workers.jobs import job, last_try, later

log = logging.getLogger(__name__)

# `[item:<id>]` and `[wiki:<slug>]` in an Ask answer, as the app reads them.
_ANSWER_MARKER_RE = re.compile(r"\s*\[(item|wiki):([^\]]+)\]")

# Times a job drafts a page again because another job changed it meanwhile.
MAX_ROUNDS = 3


class WikiBusy(Exception):
    """The user's wiki stayed locked for 30 s, or kept changing under a draft.
    The job runs again later."""


class _NotDeletedYet(Exception):
    pass


class _PageChanged(Exception):
    pass


async def lock_wiki(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Holds the user's wiki until the transaction ends. Every writer of a
    user's pages (synthesis, forget_item, compaction) writes under it, and only
    from what it reads under it: the Claude calls come first, outside any
    transaction, and the page's `version` is checked again here. So the lock
    is held for a few queries, never for a Claude call. Waits up to 30 s, then
    raises WikiBusy."""
    await session.execute(text("SET LOCAL lock_timeout = '30s'"))
    try:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"wiki:{user_id}"))))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "55P03":  # lock_not_available
            raise
        raise WikiBusy(f"wiki of {user_id} is locked") from exc
    await session.execute(text("SET LOCAL lock_timeout TO DEFAULT"))


async def read_pages(user_id: uuid.UUID, slugs: list[str]) -> dict[str, TopicPage]:
    async with get_sessionmaker()() as session:
        pages = (
            await session.execute(
                select(TopicPage).where(TopicPage.user_id == user_id, TopicPage.slug.in_(slugs))
            )
        ).scalars()
        return {p.slug: p for p in pages}


async def replace_chunks(
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


def retry_or_give_up(ctx: dict[str, Any], exc: Exception, what: str) -> None:
    """Raises Retry for a busy wiki or a transient failure, unless this is
    the last try; then the caller records that it gave up."""
    busy = isinstance(exc, WikiBusy | _NotDeletedYet)
    if (busy or is_transient(exc)) and not last_try(ctx):
        log.warning("%s: retrying later: %r", what, exc)
        raise later(ctx, 15 if busy else 60, exc) from exc
    log.error("%s: giving up: %r", what, exc, exc_info=exc)


@dataclass(frozen=True)
class _Source:
    """What a page patch needs of an item."""

    item_id: uuid.UUID
    title: str
    summary: str | None
    raw_text: str | None


@dataclass(frozen=True)
class _Snapshot(_Source):
    user_id: uuid.UUID
    topics: list[str]
    tags: list[str]
    existing_slugs: list[str]
    # Slugs other items hold that have no page yet.
    held_slugs: list[str]


# What a draft was written from: the page's version (None for no page) and
# the other item that held its slug (None when none did).
_Basis = tuple[int | None, uuid.UUID | None]


@dataclass(frozen=True)
class _Draft:
    basis: _Basis
    # None for a slug this item only holds: no page and no other item to join.
    page: PatchedPage | None
    chunks: list[tuple[str, list[float]]]


@job
async def synthesize_topics(ctx: dict[str, Any], item_id: str) -> str:
    """Fold a freshly-ingested item into AI-maintained topic pages.

    All page reads/writes are scoped to `item.user_id` so wiki synthesis
    only ever touches the owner's pages. Claude drafts each page from a copy
    read without any lock; the drafts are written together in one short
    transaction under the wiki lock, and only if none of the pages or holds
    changed since. A page that did is drafted again on top of the new copy. A
    failed try writes nothing, so a retry starts clean.

    A slug with no page doesn't get one for a single item: the item holds it
    (`items.held_topics`), and the page is written once a second item
    proposes the same slug, from both items, earlier one first.
    """
    item_uuid = uuid.UUID(item_id)
    try:
        return await _synthesize(item_uuid)
    except Exception as exc:
        retry_or_give_up(ctx, exc, f"synthesize {item_id}")
        await _set_wiki_status(item_uuid, "failed")
        return "failed"


async def _synthesize(item_id: uuid.UUID) -> str:
    snap = await _snapshot(item_id)
    if snap is None:
        log.info("synthesize: item %s not ready", item_id)
        return "skipped"
    structlog.contextvars.bind_contextvars(item_id=str(item_id), user_id=str(snap.user_id))

    proposed = await propose_slugs(
        item_title=snap.title,
        item_summary=snap.summary,
        item_topics=snap.topics,
        item_tags=snap.tags,
        existing_slugs=snap.existing_slugs,
        held_slugs=snap.held_slugs,
    )
    if not proposed.slugs:
        log.info("synthesize: no slugs proposed for %s", item_id)

    drafts: dict[str, _Draft] = {}
    for _ in range(MAX_ROUNDS):
        pages = await read_pages(snap.user_id, proposed.slugs)
        holders = await _read_holders(snap, proposed.slugs)
        # Link targets: pages, and the slugs this item is about to give pages.
        # One list for all of them, so the item's prompt block caches.
        related = list(
            dict.fromkeys(
                [*snap.existing_slugs, *(s for s in proposed.slugs if s in pages or s in holders)]
            )
        )
        for slug in proposed.slugs:
            page, holder = pages.get(slug), holders.get(slug)
            basis = (page.version if page else None, holder.item_id if holder else None)
            if slug in drafts and drafts[slug].basis == basis:
                continue
            drafts[slug] = await _draft(snap, slug, page, holder, basis, related)
        outcome = await _write_drafts(snap, proposed.slugs, drafts)
        if outcome is not None:
            return outcome
    raise WikiBusy(f"pages for {item_id} kept changing")


async def _draft(
    snap: _Snapshot,
    slug: str,
    page: TopicPage | None,
    holder: _Source | None,
    basis: _Basis,
    related: list[str],
) -> _Draft:
    # A stub (a link's `_pending_` page) has no sources yet, so it counts as
    # no page: one item alone only holds its slug.
    if (page is None or not page.source_item_ids) and holder is None:
        return _Draft(basis, None, [])
    body, title = (page.body_md, page.title) if page else ("", None)
    # The item that held the slug goes in first, so the page takes the items
    # in the order they were saved; this item's patch always runs.
    for source in (holder, snap):
        if source is None:
            continue
        patched = await patch_page(
            slug=slug,
            existing_body=body,
            existing_title=title,
            item_id=source.item_id,
            item_title=source.title,
            item_summary=source.summary,
            item_text=source.raw_text,
            related_slugs=related,
        )
        body, title = patched.body_md, patched.title
    return _Draft(basis, patched, await embed_page_chunks(patched.body_md, patched.title))


async def _snapshot(item_id: uuid.UUID) -> _Snapshot | None:
    async with get_sessionmaker()() as session:
        item = await session.get(Item, item_id)
        if item is None or item.status != "ready":
            return None
        slugs = list(
            (
                await session.execute(
                    select(TopicPage.slug).where(TopicPage.user_id == item.user_id)
                )
            ).scalars()
        )
        held = _holders(await _held(session, item.user_id, item.id))
        return _Snapshot(
            item_id=item.id,
            user_id=item.user_id,
            title=item.title or item.url,
            summary=item.summary,
            raw_text=item.raw_text,
            topics=item.topics or [],
            tags=item.tags or [],
            existing_slugs=slugs,
            held_slugs=sorted(set(held) - set(slugs)),
        )


async def _held(
    session: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID
) -> dict[uuid.UUID, list[str]]:
    """The slugs each of the user's other ready items holds, earliest item first."""
    rows = await session.execute(
        select(Item.id, Item.held_topics)
        .where(
            Item.user_id == user_id,
            Item.id != item_id,
            Item.status == "ready",
            Item.held_topics.is_not(None),
        )
        .order_by(Item.created_at, Item.id)
    )
    return {id_: slugs or [] for id_, slugs in rows}


def _holders(held: dict[uuid.UUID, list[str]]) -> dict[str, uuid.UUID]:
    """Each held slug and the earliest item holding it."""
    holders: dict[str, uuid.UUID] = {}
    for item_id, slugs in held.items():
        for slug in slugs:
            holders.setdefault(slug, item_id)
    return holders


async def _read_holders(snap: _Snapshot, slugs: list[str]) -> dict[str, _Source]:
    """The other item holding each of `slugs`, for those another item holds."""
    async with get_sessionmaker()() as session:
        holders = {
            slug: id_
            for slug, id_ in _holders(await _held(session, snap.user_id, snap.item_id)).items()
            if slug in slugs
        }
        if not holders:
            return {}
        items = (
            await session.execute(select(Item).where(Item.id.in_(set(holders.values()))))
        ).scalars()
        sources = {i.id: _Source(i.id, i.title or i.url, i.summary, i.raw_text) for i in items}
    return {slug: sources[id_] for slug, id_ in holders.items()}


async def _write_drafts(snap: _Snapshot, slugs: list[str], drafts: dict[str, _Draft]) -> str | None:
    """Writes every draft and the item's holds, or nothing: "deleted" when the
    item went while Claude wrote, None when a page or a hold changed since its
    draft."""
    async with get_sessionmaker()() as session:
        await lock_wiki(session, snap.user_id)
        # A delete after this check is cleaned up by forget_item, which waits
        # for the lock.
        if await session.scalar(select(Item.id).where(Item.id == snap.item_id)) is None:
            log.info("synthesize: item %s was deleted", snap.item_id)
            return "deleted"
        pages = {
            p.slug: p
            for p in (
                await session.execute(
                    select(TopicPage).where(
                        TopicPage.user_id == snap.user_id, TopicPage.slug.in_(slugs)
                    )
                )
            ).scalars()
        }
        held = await _held(session, snap.user_id, snap.item_id)
        holders = _holders(held)
        changed = [
            s
            for s in slugs
            if (pages[s].version if s in pages else None, holders.get(s)) != drafts[s].basis
        ]
        if changed:
            log.info("synthesize: %s changed meanwhile, drafting again", changed)
            return None
        existing = set(
            (
                await session.execute(
                    select(TopicPage.slug).where(TopicPage.user_id == snap.user_id)
                )
            ).scalars()
        )

        written = {s: p for s in slugs if (p := drafts[s].page) is not None}
        kept = [s for s in slugs if s not in written]
        joined: dict[uuid.UUID, set[str]] = {}
        for slug, patched in written.items():
            sources = [snap.item_id]
            if slug in holders:
                sources.insert(0, holders[slug])
                joined.setdefault(holders[slug], set()).add(slug)
            page = pages.get(slug)
            if page is None:
                page = TopicPage(
                    user_id=snap.user_id,
                    slug=patched.slug,
                    title=patched.title,
                    body_md=patched.body_md,
                    backlinks=patched.backlinks or None,
                    source_item_ids=sources,
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
                existing_ids += [s for s in sources if s not in existing_ids]
                page.source_item_ids = existing_ids

            await replace_chunks(session, page, drafts[slug].chunks)

            # Create stubs for any wikilink targets that don't exist yet, so
            # links resolve cleanly in the UI.
            for backlink_slug in patched.backlinks:
                if backlink_slug in written or backlink_slug in existing:
                    continue
                await session.execute(
                    pg_insert(TopicPage)
                    .values(
                        user_id=snap.user_id,
                        slug=backlink_slug,
                        title=backlink_slug.replace("-", " ").title(),
                        body_md="_pending_",
                        backlinks=None,
                        source_item_ids=None,
                        version=1,
                    )
                    .on_conflict_do_nothing(constraint="uq_topic_pages_user_slug")
                )
                existing.add(backlink_slug)

        # The earlier items whose slugs now have pages stop holding them.
        for holder, taken in joined.items():
            await session.execute(
                update(Item)
                .where(Item.id == holder)
                .values(
                    held_topics=[s for s in held[holder] if s not in taken] or None,
                    updated_at=Item.updated_at,
                )
            )
        await session.execute(
            _wiki_status_stmt(snap.item_id, "done").values(held_topics=kept or None)
        )
        await session.commit()
    log.info("synthesize: patched %s and held %s for %s", list(written), kept, snap.item_id)
    parts = [f"ok:{','.join(written)}"] if written else []
    parts += [f"held:{','.join(kept)}"] if kept else []
    return ";".join(parts) or "no_slugs"


def _wiki_status_stmt(item_id: uuid.UUID, status: str) -> Any:
    # Keeps updated_at: the item itself didn't change for the app.
    return (
        update(Item)
        .where(Item.id == item_id)
        .values(wiki_status=status, updated_at=Item.updated_at)
    )


async def _set_wiki_status(item_id: uuid.UUID, status: str) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(_wiki_status_stmt(item_id, status))
        await session.commit()


@job
async def forget_item(ctx: dict[str, Any], user_id: str, item_id: str) -> str:
    """Removes a deleted item from its owner's wiki and Ask history.

    Pages only it supported go at once, with the source cards that cite them.
    Then Claude rewrites each page that cites it or links to a page that's
    gone, drafting from a copy outside any transaction and writing under the
    wiki lock if the page hasn't changed since, and stub pages nothing links
    to any more are deleted. Safe to run again: every step looks at what is
    still there, so a retry picks up where the last try stopped. When it gives
    up, the nightly compact_user finds the item still cited and queues it
    again.
    """
    try:
        return await _forget(uuid.UUID(user_id), uuid.UUID(item_id))
    except Exception as exc:
        retry_or_give_up(ctx, exc, f"forget {item_id}")
        return "failed"


async def _forget(user_id: uuid.UUID, item_id: uuid.UUID) -> str:
    Session = get_sessionmaker()
    cite = f"[item:{item_id}]"
    structlog.contextvars.bind_contextvars(item_id=str(item_id), user_id=str(user_id))

    async with Session() as session:
        # The API queues this before it deletes, so wait for the delete to land.
        if await session.get(Item, item_id) is not None:
            raise _NotDeletedYet(str(item_id))
        await lock_wiki(session, user_id)
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
        for _ in range(MAX_ROUNDS):
            try:
                slug = await _forget_in_page(user_id, item_id, page_id)
                break
            except _PageChanged:
                continue
        else:
            raise WikiBusy(f"page {page_id} kept changing")
        if slug is not None:
            rewritten.append(slug)

    async with Session() as session:
        await lock_wiki(session, user_id)
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


async def _forget_in_page(user_id: uuid.UUID, item_id: uuid.UUID, page_id: uuid.UUID) -> str | None:
    """Rewrites one page without the item. Returns its slug once written,
    None when there was nothing to do; raises _PageChanged when the page
    changed under the draft."""
    Session = get_sessionmaker()
    async with Session() as session:
        page = await session.get(TopicPage, page_id)
        if page is None:
            return None
        alive = set(
            (await session.execute(select(TopicPage.slug).where(TopicPage.user_id == user_id)))
            .scalars()
            .all()
        )
        gone = _needs_forgetting(page.body_md, f"[item:{item_id}]", alive)
        if gone is None:
            return None
        version, body, title = page.version, page.body_md, page.title

    rewrite = await remove_from_page(body=body, item_id=item_id, gone_slugs=sorted(gone))
    body = forget_in_body(rewrite or body, item_id, gone)
    chunks_with_emb = await embed_page_chunks(body, title)

    async with Session() as session:
        await lock_wiki(session, user_id)
        page = await session.get(TopicPage, page_id)
        if page is None:
            return None
        if page.version != version:
            raise _PageChanged(str(page_id))
        page.body_md = body
        page.backlinks = sorted(set(WIKILINK_RE.findall(body)) - {page.slug}) or None
        page.version = (page.version or 1) + 1
        await replace_chunks(session, page, chunks_with_emb)
        await session.commit()
        return page.slug


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
