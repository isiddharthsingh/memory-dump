"""Embeds every saved item's and wiki page's chunks again, the way ingest and
synthesis embed them now. Run it after changing how chunks are embedded (the
model, the chunking, the context in front of each chunk):

    docker compose exec worker python -m app.workers.reembed [--user <id>]

Only Voyage is called: titles, summaries, text and pages stay as they are. An
item's chunks are rebuilt from its row as ingest builds them, and replaced
under its row lock only if it is still ready at the generation it was read at,
so an ingest that ran meanwhile wins. A page's are replaced under its owner's
wiki lock only if its version didn't move. Stub pages have no chunks and get
none. So it can run beside the worker, and running it again only embeds
everything again.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, exists, select

from app import kv
from app.ai.analyze import Analysis
from app.ai.embed import chunk_text, embed_chunks
from app.ai.synthesize import embed_page_chunks
from app.db.models import Item, ItemChunk, TopicPage, TopicPageChunk
from app.db.session import get_engine, get_sessionmaker
from app.logging_setup import configure_logging
from app.workers.ingest import _embed_context, _text_for_embed
from app.workers.synthesize import lock_wiki, replace_chunks

log = logging.getLogger(__name__)


@dataclass
class Counts:
    items: int = 0
    items_skipped: int = 0
    items_failed: int = 0
    pages: int = 0
    pages_skipped: int = 0
    pages_failed: int = 0


def _item_input(item: Item) -> tuple[str, str | None]:
    """The text ingest chunked for the item, and the context it embedded in
    front of each chunk after the first."""
    analysis = Analysis(
        title=item.title,
        summary=item.summary,
        tags=item.tags or [],
        people=item.people or [],
        handles=item.handles or [],
        brands_products=item.brands_products or [],
        topics=item.topics or [],
    )
    return _text_for_embed(analysis, item.raw_text), _embed_context(item.title, item.summary)


async def reembed_item(item_id: uuid.UUID) -> bool:
    """Replaces the item's chunks with fresh embeddings. False when it isn't
    ready, or a newer ingest moved its generation on while it was embedded."""
    Session = get_sessionmaker()
    async with Session() as session:
        item = await session.get(Item, item_id)
        if item is None or item.status != "ready":
            return False
        user_id, generation = item.user_id, item.ingest_generation
        text, context = _item_input(item)
    structlog.contextvars.bind_contextvars(user_id=str(user_id), item_id=str(item_id))

    chunks = chunk_text(text)
    embeddings = await embed_chunks(chunks, context)

    async with Session() as session:
        item = (
            await session.execute(select(Item).where(Item.id == item_id).with_for_update())
        ).scalar_one_or_none()
        if item is None or item.status != "ready" or item.ingest_generation != generation:
            return False
        await session.execute(delete(ItemChunk).where(ItemChunk.item_id == item_id))
        session.add_all(
            ItemChunk(item_id=item_id, user_id=user_id, ord=i, text=chunk, embedding=vec)
            for i, (chunk, vec) in enumerate(zip(chunks, embeddings, strict=True))
        )
        await session.commit()
    return True


async def reembed_page(page_id: uuid.UUID) -> bool:
    """Replaces the page's chunks with fresh embeddings. False when it's gone,
    or another writer changed it while it was embedded."""
    Session = get_sessionmaker()
    async with Session() as session:
        page = await session.get(TopicPage, page_id)
        if page is None:
            return False
        user_id, version, body, title = page.user_id, page.version, page.body_md, page.title
    structlog.contextvars.bind_contextvars(user_id=str(user_id), page_id=str(page_id))

    chunks = await embed_page_chunks(body, title)

    async with Session() as session:
        await lock_wiki(session, user_id)
        page = await session.get(TopicPage, page_id)
        if page is None or page.version != version:
            return False
        await replace_chunks(session, page, chunks)
        await session.commit()
    return True


async def reembed(user_id: uuid.UUID | None = None) -> Counts:
    """Every ready item and every page with chunks, or only `user_id`'s."""
    items = select(Item.id).where(Item.status == "ready").order_by(Item.created_at)
    # Stubs (`_pending_` pages nothing filled in yet) are never embedded.
    pages = (
        select(TopicPage.id)
        .where(exists().where(TopicPageChunk.topic_page_id == TopicPage.id))
        .order_by(TopicPage.updated_at)
    )
    if user_id is not None:
        items = items.where(Item.user_id == user_id)
        pages = pages.where(TopicPage.user_id == user_id)
    async with get_sessionmaker()() as session:
        item_ids = list((await session.execute(items)).scalars())
        page_ids = list((await session.execute(pages)).scalars())
        await session.commit()

    counts = Counts()
    # Background work: it takes the jobs' share of Voyage's slots
    # (app/ai/clients.py), leaving the rest to Search and Ask.
    structlog.contextvars.bind_contextvars(job="reembed")
    for item_id in item_ids:
        try:
            if await reembed_item(item_id):
                counts.items += 1
            else:
                counts.items_skipped += 1
        except Exception:
            counts.items_failed += 1
            log.exception("reembed: item %s failed", item_id)
        finally:
            structlog.contextvars.unbind_contextvars("user_id", "item_id")
    for page_id in page_ids:
        try:
            if await reembed_page(page_id):
                counts.pages += 1
            else:
                counts.pages_skipped += 1
        except Exception:
            counts.pages_failed += 1
            log.exception("reembed: page %s failed", page_id)
        finally:
            structlog.contextvars.unbind_contextvars("user_id", "page_id")
    structlog.contextvars.unbind_contextvars("job")

    log.info(
        "reembed: items %d embedded, %d skipped, %d failed; "
        "pages %d embedded, %d skipped, %d failed",
        counts.items,
        counts.items_skipped,
        counts.items_failed,
        counts.pages,
        counts.pages_skipped,
        counts.pages_failed,
    )
    return counts


async def _main(user_id: uuid.UUID | None) -> Counts:
    try:
        return await reembed(user_id)
    finally:
        await kv.close()
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.reembed",
        description="Embed saved items' and wiki pages' chunks again.",
    )
    parser.add_argument("--user", type=uuid.UUID, help="only this user's items and pages")
    args = parser.parse_args(argv)
    configure_logging()
    counts = asyncio.run(_main(args.user))
    return 1 if counts.items_failed or counts.pages_failed else 0


if __name__ == "__main__":
    sys.exit(main())
