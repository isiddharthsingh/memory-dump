"""Wiki synthesis layer (Karpathy LLM-wiki pattern).

For each newly-ingested item:
1. Ask Claude Haiku to propose 1-4 topic slugs the item belongs to, given
   the slugs that have pages and the slugs other items are waiting on.
2. For each slug, patch (or create) the topic page with Claude Sonnet,
   preserving existing content and adding [[wikilinks]] + [item:UUID]
   citations. The worker (app/workers/synthesize.py) decides which slugs get
   a page: a new one only once two items support it.
3. Re-chunk and re-embed the patched body for retrieval.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from slugify import slugify

from app.ai.clients import claude, claude_json
from app.ai.embed import chunk_text, embed_chunks
from app.config import get_settings
from app.errors import Refused

PROPOSE_SYSTEM = """You file a new item into a personal wiki of topic \
pages, each of which gathers what several saved items say about one topic. \
Choose the topic slugs this item belongs under.

You get two lists:
- Existing pages. Strongly prefer these: when one covers the item's subject,
  even broadly, use it rather than a narrower new slug.
- Waiting topics: slugs chosen for earlier items that have no page yet. A
  page is only written once a second item lands on the same slug, so when
  one of these fits, return it exactly as written.

Propose a brand-new slug only when nothing in either list fits, and never
more than two. Return 1-4 slugs in all; 1-2 is usually right.

A slug is lowercase and hyphenated, and names a topic several items could
share: specific enough to mean something ("rust-async-runtimes", not
"programming"), broad enough to hold more than this one item (never the
item's own title).
"""

PATCH_SYSTEM = """You maintain a personal wiki of interlinked topic \
pages. You receive details about a NEW source item and the slugs you may \
link to, then a page's existing markdown (which may be empty). You must \
produce the updated markdown for the page.

Style:
- Concise prose, occasional bullets. Sections with `## Headings` as the
  page grows.
- Every factual claim cites the source item it came from using
  `[item:UUID]` (literal, the UUID is provided).
- Use `[[other-slug]]` wikilinks when a related topic is referenced.
  Prefer linking to existing slugs (a list is provided) over inventing
  new ones.
- Preserve existing content unless the new item contradicts it. Do not
  duplicate facts the page already states; integrate the new perspective.
- Do NOT print citations the user did not ask about; only cite the new
  item and previously-cited items already present in the page.
- Output ONLY the full new markdown body. No fences, no commentary.
"""

FORGET_SYSTEM = """You maintain a personal wiki of interlinked topic \
pages. The user deleted one of the source items, and the page must keep no \
trace of it. You receive the page's markdown, the deleted item's citation \
and the slugs of topic pages that no longer exist.

Rewrite the page:
- Remove every statement cited only to the deleted item, and any uncited
  text that only introduces or frames that material (headings, lead-ins,
  "see also" lines).
- Where a statement also cites other items, keep what those items support
  and drop the deleted item's citation.
- Remove `[[slug]]` links to the listed slugs, rewording or dropping the
  text around them so it still reads well.
- Remove headings left with nothing under them.
- Change nothing else: keep the remaining wording, citations, links and
  structure as they are, and add nothing new.
- Output ONLY the full new markdown body. No fences, no commentary.
"""

MAX_ITEM_CHARS = 6_000
MAX_PAGE_CHARS = 12_000


@dataclass
class ProposedSlugs:
    slugs: list[str]


@dataclass
class PatchedPage:
    slug: str
    title: str
    body_md: str
    backlinks: list[str]


WIKILINK_RE = re.compile(r"\[\[([a-z0-9][a-z0-9\-]*)\]\]")
CITATION_RE = re.compile(r"\[item:([0-9a-fA-F-]{36})\]")
# The citations that close a claim, and the punctuation after them.
_CITES_RE = re.compile(r"(?:\s*\[item:[0-9a-fA-F-]{36}\])+[.,;:!?)]*")
# A line's indent plus its list or quote marker, kept when its text changes.
_LINE_PREFIX_RE = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+|>\s*)?")
_HEADING_RE = re.compile(r"^(#{1,6})\s")


def _normalize_slug(s: str) -> str:
    return slugify(s, max_length=80, lowercase=True)


_ACRONYMS = {
    a.lower(): a
    for a in (
        "AI",
        "LLM",
        "LLMs",
        "ML",
        "NLP",
        "API",
        "GPU",
        "RAG",
        "UI",
        "UX",
        "SQL",
        "GPT",
        "RL",
        "RLHF",
        "iOS",
        "PDF",
    )
}


def title_from_slug(slug: str) -> str:
    """'llm-scaling' -> 'LLM Scaling' (str.title() would give 'Llm Scaling')."""
    return " ".join(_ACRONYMS.get(w, w.capitalize()) for w in slug.split("-") if w)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[...truncated...]"


_SLUGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"slugs": {"type": "array", "items": {"type": "string"}}},
    "required": ["slugs"],
    "additionalProperties": False,
}
# Brand-new slugs one item may bring, on top of existing and waiting ones.
MAX_NEW_SLUGS = 2


async def propose_slugs(
    *,
    item_title: str,
    item_summary: str | None,
    item_topics: list[str],
    item_tags: list[str],
    existing_slugs: list[str],
    held_slugs: list[str],
) -> ProposedSlugs:
    settings = get_settings()

    item_facts = (
        f"Item title: {item_title}\n"
        f"Summary: {item_summary or '(none)'}\n"
        f"Topics: {', '.join(item_topics) or '(none)'}\n"
        f"Tags: {', '.join(item_tags) or '(none)'}\n\n"
        f"Existing pages: {', '.join(existing_slugs[:300]) or '(none yet)'}\n"
        f"Waiting topics: {', '.join(held_slugs[:300]) or '(none)'}"
    )
    data = await claude_json(
        purpose="propose_slugs",
        schema=_SLUGS_SCHEMA,
        effort="low",
        model=settings.claude_analysis_model,
        max_tokens=4_000,
        system=PROPOSE_SYSTEM,
        messages=[{"role": "user", "content": item_facts}],
    )

    known = {*existing_slugs, *held_slugs}
    cleaned: list[str] = []
    new = 0
    for s in data.get("slugs") or []:
        norm = _normalize_slug(s) if isinstance(s, str) else ""
        if not norm or norm in cleaned:
            continue
        # The prompt asks for few new slugs; this makes it so.
        if norm not in known:
            if new >= MAX_NEW_SLUGS:
                continue
            new += 1
        cleaned.append(norm)
        if len(cleaned) >= 4:
            break
    return ProposedSlugs(slugs=cleaned)


async def patch_page(
    *,
    slug: str,
    existing_body: str | None,
    existing_title: str | None,
    item_id: uuid.UUID,
    item_title: str,
    item_summary: str | None,
    item_text: str | None,
    related_slugs: list[str],
) -> PatchedPage:
    """The page with the item worked in. `related_slugs` should be the same
    list for every page one item patches: the item's block and that list come
    first, under a cache breakpoint, so patching its second, third and fourth
    page reads them from the prompt cache."""
    settings = get_settings()

    title = existing_title or title_from_slug(slug)
    body = existing_body or ""
    related_text = ", ".join(related_slugs[:80]) if related_slugs else "(none yet)"

    item_block = (
        f"=== New item ===\n"
        f"item_id: {item_id}\n"
        f"Title: {item_title}\n"
        f"Summary: {item_summary or '(none)'}\n"
        f"Raw text:\n{_truncate(item_text or '', MAX_ITEM_CHARS)}\n\n"
        f"=== Available wikilink targets ===\n{related_text}"
    )
    page_block = (
        f"Page slug: {slug}\n"
        f"Page title: {title}\n\n"
        f"=== Current body (markdown) ===\n{_truncate(body, MAX_PAGE_CHARS)}\n\n"
        "Produce the full updated markdown for this page. Output ONLY the markdown."
    )

    resp = await claude(
        purpose="patch_page",
        effort="medium",
        model=settings.claude_synthesis_model,
        max_tokens=16_000,
        system=PATCH_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": item_block, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": page_block},
                ],
            }
        ],
    )
    if resp.stop_reason == "max_tokens":
        # A page cut off mid-sentence would replace the whole page.
        raise ValueError(f"patch_page {slug}: the answer was cut off at max_tokens")
    new_body = "".join(b.text for b in resp.content if b.type == "text").strip()
    # Strip any code fences the model added by mistake.
    new_body = re.sub(r"^```(?:markdown)?\s*", "", new_body)
    new_body = re.sub(r"\s*```$", "", new_body).strip()
    if not new_body:
        new_body = body  # don't lose the page if the model returns empty

    backlinks = sorted({m.group(1) for m in WIKILINK_RE.finditer(new_body)} - {slug})

    return PatchedPage(
        slug=slug,
        title=title,
        body_md=new_body,
        backlinks=backlinks,
    )


async def remove_from_page(*, body: str, item_id: uuid.UUID, gone_slugs: list[str]) -> str | None:
    """The page rewritten without a deleted item and without links to deleted
    pages, or None when Claude's answer is empty, cut off or refused; the
    caller's forget_in_body does the work then."""
    settings = get_settings()

    user = (
        f"Deleted item: [item:{item_id}]\n"
        f"Pages that no longer exist: {', '.join(gone_slugs) or '(none)'}\n\n"
        f"=== Page (markdown) ===\n{body}"
    )
    try:
        resp = await claude(
            purpose="forget",
            effort="medium",
            model=settings.claude_synthesis_model,
            max_tokens=16_000,
            system=FORGET_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except Refused:
        return None
    if resp.stop_reason != "end_turn":
        return None
    new_body = "".join(b.text for b in resp.content if b.type == "text").strip()
    new_body = re.sub(r"^```(?:markdown)?\s*", "", new_body)
    new_body = re.sub(r"\s*```$", "", new_body).strip()
    return new_body or None


def forget_in_body(body: str, item_id: uuid.UUID, gone_slugs: set[str]) -> str:
    """Takes out whatever a rewrite left of a deleted item: claims cited only
    to it, its citations on shared claims, links to deleted pages, and the
    headings this empties. Lines it doesn't touch stay as they are."""
    item = str(item_id).lower()

    def unlink(text: str, label: bool) -> str:
        return WIKILINK_RE.sub(
            lambda m: (
                (title_from_slug(m.group(1)) if label else "")
                if m.group(1) in gone_slugs
                else m.group(0)
            ),
            text,
        )

    lines: list[str] = []
    for line in body.split("\n"):
        rest = _LINE_PREFIX_RE.sub("", line, count=1)
        prefix = line[: len(line) - len(rest)]
        text = _strip_claims(rest, item)
        if text == rest and unlink(text, label=False) == text:
            lines.append(line)
            continue
        # Nothing left but the item's claims or links to deleted pages.
        if not unlink(text, label=False).strip(" \t.,;:—–-"):
            continue
        lines.append(prefix + unlink(text, label=True).lstrip())

    out = "\n".join(_drop_empty_headings(lines))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _strip_claims(text: str, item: str) -> str:
    """Drops each claim whose citations are only `item`, and `item`'s
    citation from claims that other items support too."""
    out: list[str] = []
    start = 0
    for m in _CITES_RE.finditer(text):
        cited = {c.lower() for c in CITATION_RE.findall(m.group())}
        if cited != {item}:
            cites = re.sub(rf"\s*\[item:{re.escape(item)}\]", "", m.group(), flags=re.IGNORECASE)
            out.append(text[start : m.start()] + cites)
        start = m.end()
    out.append(text[start:])
    return "".join(out)


def _drop_empty_headings(lines: list[str]) -> list[str]:
    """Headings with no text before the next heading of their level or above."""
    kept: list[str] = []
    for i, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            for after in lines[i + 1 :]:
                sub = _HEADING_RE.match(after)
                if sub and len(sub.group(1)) <= level:
                    break
                if not sub and after.strip():
                    kept.append(line)
                    break
            continue
        kept.append(line)
    return kept


async def embed_page_chunks(body: str, title: str) -> list[tuple[str, list[float]]]:
    """A page's chunks with their embeddings; chunks after the first are
    embedded with the page's title in front, so they say what they're about."""
    chunks = chunk_text(body)
    if not chunks:
        return []
    embs = await embed_chunks(chunks, title)
    return list(zip(chunks, embs, strict=True))
