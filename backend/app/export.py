"""The library as an Obsidian vault: one Markdown note per saved item and per
wiki page.

Pages keep their `[[slug]]` links, which Obsidian resolves by file name, so
each page's file is its slug. An `[item:<id>]` citation becomes a numbered
link to the item's note, numbered in the order the page first cites each
item, as the app shows them. Frontmatter values are written as JSON, which is
YAML, so no title or tag can break it.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, tzinfo
from typing import Any

from slugify import slugify

from app.db.models import Item, TopicPage

ROOT = "Memory Dump"
_CITATION_RE = re.compile(r"[ \t]*\[item:([0-9a-fA-F-]{36})\]")


def item_name(item_id: uuid.UUID, title: str | None) -> str:
    """An item note's file name, without `.md`: its title as a slug, and the
    start of its id so two items with one title stay apart."""
    return f"{slugify(title or '', max_length=60) or 'item'}-{str(item_id)[:8]}"


def _frontmatter(fields: Mapping[str, Any]) -> str:
    lines = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n"


def _day(at: datetime | None, tz: tzinfo) -> str | None:
    return at.astimezone(tz).date().isoformat() if at else None


def _details(details: dict[str, Any]) -> list[str]:
    sections = []
    if places := details.get("places"):
        lines = [
            f"- {p['name']}" + (f", {p['address']}" if p.get("address") else "") for p in places
        ]
        sections.append("## Places\n\n" + "\n".join(lines))
    if ingredients := details.get("ingredients"):
        sections.append("## Ingredients\n\n" + "\n".join(f"- {i}" for i in ingredients))
    if steps := details.get("steps"):
        sections.append("## Steps\n\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)))
    if price := details.get("price"):
        sections.append(f"Price: {price}")
    return sections


def item_note(item: Item, tz: tzinfo) -> tuple[str, str]:
    """The item's path in the vault and its note."""
    title = item.title or item.url
    uploaded = item.url.startswith("file://")
    fields: dict[str, Any] = {
        "title": title,
        "url": None if uploaded else item.url,
        "source": item.source,
        "kind": item.kind,
        "saved": _day(item.created_at, tz),
        "done": _day(item.done_at, tz),
        "tags": item.tags or None,
    }
    parts = [f"# {title}"]
    if not uploaded:
        parts.append(f"[Open the original]({item.url})")
    if item.share_text:
        quoted = "\n".join(f"> {line}" for line in item.share_text.splitlines())
        parts.append(f"> [!note] My note\n{quoted}")
    if item.summary:
        parts.append(f"## Summary\n\n{item.summary}")
    parts += _details(item.details or {})
    if item.raw_text:
        parts.append(f"## Text\n\n{item.raw_text}")
    body = "\n\n".join(parts) + "\n"
    kept = {key: value for key, value in fields.items() if value is not None}
    return f"{ROOT}/Items/{item_name(item.id, item.title)}.md", _frontmatter(kept) + "\n" + body


def page_note(
    page: TopicPage, items: Mapping[uuid.UUID, str | None], tz: tzinfo
) -> tuple[str, str]:
    """The page's path in the vault and its note. `items` holds the title of
    every item of the user's that is exported; a citation of any other item
    (one deleted since) is dropped."""
    order: list[uuid.UUID] = []

    def cite(match: re.Match[str]) -> str:
        try:
            item_id = uuid.UUID(match.group(1))
        except ValueError:
            return ""
        if item_id not in items:
            return ""
        if item_id not in order:
            order.append(item_id)
        return f" [[{item_name(item_id, items[item_id])}|{order.index(item_id) + 1}]]"

    body = _CITATION_RE.sub(cite, page.body_md).strip()
    parts = [f"# {page.title}", body]
    if order:
        sources = "\n".join(
            f"{n}. [[{item_name(i, items[i])}|{items[i] or 'Untitled'}]]"
            for n, i in enumerate(order, 1)
        )
        parts.append(f"## Sources\n\n{sources}")
    if page.corrections:
        fixes = "\n".join(f"- {' '.join(c['text'].split())}" for c in page.corrections)
        parts.append(f"## My corrections\n\n{fixes}")
    fields = {"title": page.title, "aliases": [page.title], "updated": _day(page.updated_at, tz)}
    return f"{ROOT}/Wiki/{page.slug}.md", _frontmatter(fields) + "\n" + "\n\n".join(parts) + "\n"
