"""What Ask can look up while it answers: another search, a list of saved
items, one item or wiki page in full.

Ask's first search finds a handful of passages, which answers a question about
one thing. These are for the questions it can't: every item of a kind, how
many were saved in a period, a comparison across items, a passage that was cut
short. Every lookup reads only the user's own rows, and only the scope's when
the question is about one item or one wiki page.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from sqlalchemy import DateTime, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.analyze import KINDS
from app.ai.retrieval import SOURCES, Candidate, Filters, Scope, hybrid_search
from app.db.models import Item, TopicPage

# Passages one search returns, at most PER_SOURCE of them from one item or page.
SEARCH_CHUNKS = 8
PER_SOURCE = 2
# Items one list_items call returns; `offset` reaches the rest.
LIST_ITEMS = 40
SUMMARY_CHARS = 240
# How much of an item's text or a page's body `read` returns.
READ_CHARS = 12_000
# Lookups one turn may make. Every answer is sent again with each later
# round, so a turn that asks for more is told to ask again for the rest.
MAX_CALLS = 6

_DATE = {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]}
_SOURCE = {"anyOf": [{"type": "string", "enum": list(SOURCES)}, {"type": "null"}]}
_KIND = {"anyOf": [{"type": "string", "enum": list(KINDS)}, {"type": "null"}]}


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    """A tool whose input always matches its schema (`strict`)."""
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


_FILTERS = {
    "source": _SOURCE | {"description": "Only saved items from this source, or null."},
    "kind": _KIND
    | {
        "description": "Only saved items of this kind, or null: place (somewhere to "
        "go), recipe, product (something to buy), media (a film, book, podcast or "
        "game), howto (a tutorial or tip), other."
    },
    "since": _DATE | {"description": "Only items saved on or after this day, or null."},
    "until": _DATE | {"description": "Only items saved on or before this day, or null."},
}

TOOLS: list[dict[str, Any]] = [
    _tool(
        "search",
        "Search the library by meaning and keywords. Returns the best-matching "
        "passages of saved items and wiki pages, each under its [item:…] or "
        "[wiki:…] marker. Use it to look for something the first passages don't "
        "cover: other words, a name, a narrower topic. With any filter set, wiki "
        "pages are left out.",
        {"query": {"type": "string", "description": "What to look for."}, **_FILTERS},
    ),
    _tool(
        "list_items",
        f"List saved items newest first, {LIST_ITEMS} at a time, each with its "
        "marker, title, kind, source, save date and summary, and the number that "
        "match in all. Use it for questions about many items at once: everything "
        "of one kind or source, everything saved in a period, how many there are. "
        "All filters null lists the whole library.",
        {
            **_FILTERS,
            "tag": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Only items with exactly this tag (lowercase, hyphenated), or null.",
            },
            "done": {
                "anyOf": [{"type": "boolean"}, {"type": "null"}],
                "description": "true for items the user marked done (been there, cooked "
                "it, bought it), false for the ones not yet, null for both.",
            },
            "offset": {"type": "integer", "description": "Items to skip; 0 for the first page."},
        },
    ),
    _tool(
        "read",
        "Read one saved item in full (its text, the user's note, its places, "
        "ingredients, steps or price) or one wiki page, when a passage is cut "
        "short or a summary isn't enough.",
        {
            "ref": {
                "type": "string",
                "description": "The marker without brackets: item:<id> or wiki:<slug>.",
            }
        },
    ),
]

_SOURCE_NAMES = {
    "twitter": "a post on X (Twitter)",
    "instagram": "an Instagram post",
    "youtube": "a YouTube video",
    "reddit": "a Reddit post",
    "web": "a web page",
    "pdf": "a PDF",
    "image": "an image",
}


# Each kind's saves as the app's step line names them ("Listing your places").
_KIND_NAMES = {
    "place": "places",
    "recipe": "recipes",
    "product": "things to buy",
    "media": "things to watch and read",
    "howto": "how-tos",
}


def source_name(source: str | None) -> str:
    return _SOURCE_NAMES.get(source or "", "an item")


def _day(at: datetime | None, tz: tzinfo) -> str:
    return at.astimezone(tz).date().isoformat() if at else "an unknown day"


def format_chunks(chunks: list[Candidate], tz: tzinfo) -> str:
    """Passages as Ask reads them: each under its marker, what it comes from
    and the day it was saved (a wiki page: updated)."""
    blocks = []
    for c in chunks:
        if c.kind == "item":
            head = f"[item:{c.parent_id}] {c.title or '(untitled)'}"
            if c.url:
                head += f" — {c.url}"
            kind = source_name(c.source)
            head += f"\n{kind[0].upper()}{kind[1:]}, saved {_day(c.at, tz)}"
        else:
            head = f"[wiki:{c.slug}] {c.title or '(untitled)'}"
            head += f"\nA wiki page, updated {_day(c.at, tz)}"
        blocks.append(f"{head}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _details(details: dict[str, Any] | None) -> list[str]:
    """An item's places, ingredients, steps and price as lines."""
    details = details or {}
    lines = []
    for place in details.get("places") or []:
        address = f", {place['address']}" if place.get("address") else ""
        lines.append(f"Place: {place['name']}{address}")
    if details.get("ingredients"):
        lines.append("Ingredients: " + "; ".join(details["ingredients"]))
    for n, step in enumerate(details.get("steps") or [], 1):
        lines.append(f"Step {n}: {step}")
    if details.get("price"):
        lines.append(f"Price: {details['price']}")
    return lines


@dataclass
class Toolbox:
    """Runs Ask's lookups for one user, inside `scope` when there is one."""

    session: AsyncSession
    user_id: uuid.UUID
    scope: Scope | None
    tz: tzinfo

    def describe(self, name: str, args: dict[str, Any]) -> str:
        """What a lookup is doing, for the app to show while it runs."""
        if name == "search":
            return f"Searching for “{_cut(str(args.get('query') or ''), 60)}”"
        if name == "list_items":
            kind = _KIND_NAMES.get(str(args.get("kind")))
            return f"Listing your {kind}" if kind else "Listing what you saved"
        if name == "read":
            wiki = str(args.get("ref") or "").startswith("wiki:")
            return "Reading a wiki page" if wiki else "Reading a save"
        return "Looking something up"

    async def run(self, name: str, args: dict[str, Any]) -> str:
        """The lookup's answer as text. LookupError when it names something
        the user doesn't have; its message goes back to Claude."""
        try:
            if name == "search":
                return await self._search(args)
            if name == "list_items":
                return await self._list(args)
            if name == "read":
                return await self._read(str(args.get("ref") or ""))
            raise LookupError(f"There is no tool called {name}.")
        finally:
            # No pooled connection is held while Claude reads the answer.
            await self.session.commit()

    def _filters(self, args: dict[str, Any]) -> Filters:
        since, until = _date(args.get("since")), _date(args.get("until"))
        if since and until and since > until:
            since, until = until, since
        source, kind = args.get("source"), args.get("kind")
        return Filters(
            source=source if source in SOURCES else None,
            kind=kind if kind in KINDS else None,
            since=since,
            until=until,
            tz=self.tz,
        )

    async def _search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            raise LookupError("search needs a query.")
        chunks = await hybrid_search(
            self.session,
            query,
            self.user_id,
            final_limit=SEARCH_CHUNKS,
            scope=self.scope,
            filters=self._filters(args) or None,
            per_parent=None if self.scope is not None and self.scope.one_item else PER_SOURCE,
        )
        return format_chunks(chunks, self.tz) if chunks else "Nothing saved matches that."

    async def _list(self, args: dict[str, Any]) -> str:
        filters = self._filters(args)
        where = [Item.user_id == self.user_id, Item.status == "ready"]
        if self.scope is not None:
            where.append(Item.id.in_(self.scope.item_ids))
        if filters.source:
            where.append(Item.source == filters.source)
        if filters.kind:
            where.append(Item.kind == filters.kind)
        if isinstance(args.get("tag"), str) and args["tag"].strip():
            where.append(Item.tags.any(args["tag"].strip()))  # type: ignore[arg-type]
        if isinstance(args.get("done"), bool):
            where.append(Item.done_at.is_not(None) if args["done"] else Item.done_at.is_(None))
        # The model maps items.created_at as a naive timestamp, but the column
        # is timestamptz: bind the days' ends as one.
        if filters.since:
            start = datetime.combine(filters.since, time(), self.tz)
            where.append(Item.created_at >= literal(start, DateTime(timezone=True)))
        if filters.until:
            end = datetime.combine(filters.until + timedelta(days=1), time(), self.tz)
            where.append(Item.created_at < literal(end, DateTime(timezone=True)))

        offset = max(int(args.get("offset") or 0), 0)
        total = await self.session.scalar(select(func.count()).select_from(Item).where(*where))
        rows = (
            await self.session.execute(
                select(
                    Item.id,
                    Item.title,
                    Item.url,
                    Item.source,
                    Item.kind,
                    Item.summary,
                    Item.done_at,
                    Item.created_at,
                )
                .where(*where)
                .order_by(Item.created_at.desc(), Item.id.desc())
                .limit(LIST_ITEMS)
                .offset(offset)
            )
        ).all()
        if not rows:
            return f"{total} saved items match; none from {offset + 1} on."
        lines = [f"{total} saved items match; these are {offset + 1} to {offset + len(rows)}."]
        for row in rows:
            facts = [source_name(row.source), f"saved {_day(row.created_at, self.tz)}"]
            if row.kind and row.kind != "other":
                facts.insert(0, row.kind)
            if row.done_at is not None:
                facts.append("marked done")
            lines.append(f"[item:{row.id}] {row.title or row.url} ({', '.join(facts)})")
            if row.summary:
                lines.append("  " + _cut(" ".join(row.summary.split()), SUMMARY_CHARS))
        return "\n".join(lines)

    async def _read(self, ref: str) -> str:
        kind, _, name = ref.strip().strip("[]").partition(":")
        if kind == "wiki":
            return await self._read_page(name)
        try:
            item_id = uuid.UUID(name)
        except ValueError:
            raise LookupError("read takes item:<id> or wiki:<slug>.") from None
        if kind != "item":
            raise LookupError("read takes item:<id> or wiki:<slug>.")
        item = await self.session.get(Item, item_id)
        outside = self.scope is not None and item_id not in self.scope.item_ids
        if item is None or item.user_id != self.user_id or item.status != "ready" or outside:
            raise LookupError("There is no such saved item.")
        source = source_name(item.source)
        lines = [
            f"[item:{item.id}] {item.title or item.url}",
            f"{source[0].upper()}{source[1:]}, saved {_day(item.created_at, self.tz)}"
            + (", marked done" if item.done_at is not None else ""),
        ]
        if not item.url.startswith("file://"):
            lines.append(f"URL: {item.url}")
        if item.kind:
            lines.append(f"Kind: {item.kind}")
        lines += _details(item.details)
        if item.share_text:
            lines.append(f"The user's note: {item.share_text}")
        if item.summary:
            lines.append(f"Summary: {item.summary}")
        if item.raw_text:
            lines.append("\n" + _cut(item.raw_text, READ_CHARS))
        return "\n".join(lines)

    async def _read_page(self, slug: str) -> str:
        page = (
            await self.session.execute(
                select(TopicPage).where(TopicPage.user_id == self.user_id, TopicPage.slug == slug)
            )
        ).scalar_one_or_none()
        if page is None or (self.scope is not None and page.id not in self.scope.page_ids):
            raise LookupError("There is no such wiki page.")
        return (
            f"[wiki:{page.slug}] {page.title}\n"
            f"A wiki page, updated {_day(page.updated_at, self.tz)}\n\n"
            + _cut(page.body_md, READ_CHARS)
        )


@dataclass
class StreamStep:
    """A lookup under way ("Listing your places"), for the app to show
    until the answer starts."""

    text: str


def echo(content: list[Any]) -> list[Any]:
    """An assistant turn as it is sent back with its lookups' answers:
    unchanged, thinking blocks included. When a fallback model took over
    mid-turn, the thinking and tool calls from before its `fallback` block
    are left out, as the API asks."""
    last = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    dropped = ("thinking", "redacted_thinking", "tool_use")
    return [b for i, b in enumerate(content) if i >= last or b.type not in dropped]


async def lookups(toolbox: Toolbox, calls: list[Any]) -> AsyncIterator[StreamStep | dict[str, Any]]:
    """Runs a turn's lookups one after another (they share a database
    session), yielding what each is doing and then its `tool_result`."""
    for n, call in enumerate(calls):
        result: dict[str, Any] = {"type": "tool_result", "tool_use_id": call.id}
        if n >= MAX_CALLS:
            # Every call still gets an answer: the API takes no turn without one.
            yield result | {
                "content": f"Only {MAX_CALLS} lookups at a time. Ask again for this one.",
                "is_error": True,
            }
            continue
        args = call.input if isinstance(call.input, dict) else {}
        yield StreamStep(text=toolbox.describe(call.name, args))
        try:
            result["content"] = await toolbox.run(call.name, args)
        except LookupError as exc:
            result |= {"content": str(exc), "is_error": True}
        yield result
