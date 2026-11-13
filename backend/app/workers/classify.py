"""Gives every item analysed before kinds were kept its kind and details
(app/ai/analyze.py), from what is saved of it:

    docker compose exec worker python -m app.workers.classify [--user <id>] [--limit <n>]

Only ready items without a kind are read, so it can stop and start again, and
new saves get theirs from ingest. One Claude call per item
(CLAUDE_ANALYSIS_MODEL); nothing is embedded again. An item is written under
its row lock only if it is still ready at the generation it was read at, so an
ingest that ran meanwhile wins.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

import structlog
from sqlalchemy import select

from app import kv
from app.ai.analyze import classify
from app.db.models import Item
from app.db.session import get_engine, get_sessionmaker
from app.errors import Refused
from app.logging_setup import configure_logging

log = logging.getLogger(__name__)


async def classify_item(item_id: uuid.UUID) -> bool:
    """False when the item isn't ready, already has a kind, or a newer ingest
    moved its generation on while Claude read it."""
    Session = get_sessionmaker()
    async with Session() as session:
        item = await session.get(Item, item_id)
        if item is None or item.status != "ready" or item.kind is not None:
            return False
        generation = item.ingest_generation
        title, summary, raw_text = item.title, item.summary, item.raw_text
        url, source = item.url, item.source
        structlog.contextvars.bind_contextvars(user_id=str(item.user_id), item_id=str(item_id))

    try:
        kind, details = await classify(
            title=title, summary=summary, raw_text=raw_text, url=url, source=source
        )
    except Refused:
        # Asking again gets the same answer; "other" keeps it out of the next run.
        kind, details = "other", None

    async with Session() as session:
        item = (
            await session.execute(select(Item).where(Item.id == item_id).with_for_update())
        ).scalar_one_or_none()
        if item is None or item.status != "ready" or item.ingest_generation != generation:
            return False
        item.kind = kind
        item.details = details
        await session.commit()
    return True


async def classify_all(user_id: uuid.UUID | None = None, limit: int | None = None) -> int:
    """Classifies the ready items that have no kind; returns how many failed."""
    stmt = (
        select(Item.id)
        .where(Item.status == "ready", Item.kind.is_(None))
        .order_by(Item.created_at.desc())
    )
    if user_id is not None:
        stmt = stmt.where(Item.user_id == user_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    async with get_sessionmaker()() as session:
        item_ids = list((await session.execute(stmt)).scalars())
        await session.commit()

    done = skipped = failed = 0
    # Background work: it takes the jobs' share of Claude's slots
    # (app/ai/clients.py), leaving the rest to Ask.
    structlog.contextvars.bind_contextvars(job="classify")
    for item_id in item_ids:
        try:
            if await classify_item(item_id):
                done += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
            log.exception("classify: item %s failed", item_id)
        finally:
            structlog.contextvars.unbind_contextvars("user_id", "item_id")
    structlog.contextvars.unbind_contextvars("job")
    log.info("classify: %d classified, %d skipped, %d failed", done, skipped, failed)
    return failed


async def _main(user_id: uuid.UUID | None, limit: int | None) -> int:
    try:
        return await classify_all(user_id, limit)
    finally:
        await kv.close()
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.classify",
        description="Give saved items from before kinds their kind and details.",
    )
    parser.add_argument("--user", type=uuid.UUID, help="only this user's items")
    parser.add_argument("--limit", type=int, help="at most this many items, newest first")
    args = parser.parse_args(argv)
    configure_logging()
    return 1 if asyncio.run(_main(args.user, args.limit)) else 0


if __name__ == "__main__":
    sys.exit(main())
