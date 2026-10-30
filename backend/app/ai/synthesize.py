"""Wiki synthesis layer (Karpathy LLM-wiki pattern).

For each newly-ingested item:
1. Ask Claude Haiku to propose 1-4 topic slugs the item belongs to,
   given the list of existing slugs.
2. For each slug, patch (or create) the topic page with Claude Sonnet,
   preserving existing content and adding [[wikilinks]] + [item:UUID]
   citations.
3. Re-chunk and re-embed the patched body for retrieval.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from slugify import slugify

from app.ai.clients import claude
from app.ai.embed import chunk_text, embed_texts
from app.config import get_settings

PROPOSE_SYSTEM = """You file new pieces of information into a personal \
wiki of topic pages. Given a new item and a list of existing topic slugs, \
choose 1-4 slugs (existing or new) where this item belongs.

Return STRICT JSON: {"slugs": ["slug-one", "slug-two"]}

Rules:
- Slugs are lowercase, hyphenated, specific (e.g. "rust-async-runtimes"
  not "programming").
- Prefer reusing an existing slug if it fits well.
- Up to 4 slugs. Usually 1-2 is right.
"""

PATCH_SYSTEM = """You maintain a personal wiki of interlinked topic \
pages. You receive the existing markdown for a page (which may be empty) \
plus details about a NEW source item. You must produce the updated \
markdown for the page.

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


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


async def propose_slugs(
    *,
    item_title: str,
    item_summary: str | None,
    item_topics: list[str],
    item_tags: list[str],
    existing_slugs: list[str],
) -> ProposedSlugs:
    settings = get_settings()

    existing_text = ", ".join(existing_slugs[:300]) if existing_slugs else "(none yet)"
    item_facts = (
        f"Item title: {item_title}\n"
        f"Summary: {item_summary or '(none)'}\n"
        f"Topics: {', '.join(item_topics) or '(none)'}\n"
        f"Tags: {', '.join(item_tags) or '(none)'}\n\n"
        f"Existing slugs: {existing_text}\n\n"
        "Return JSON only."
    )

    resp = await claude(
        purpose="propose_slugs",
        model=settings.claude_analysis_model,
        max_tokens=200,
        system=[
            {"type": "text", "text": PROPOSE_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": item_facts}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    raw = _strip_json_fence(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ProposedSlugs(slugs=[])

    slugs_raw = data.get("slugs") or []
    cleaned: list[str] = []
    for s in slugs_raw:
        if not isinstance(s, str):
            continue
        norm = _normalize_slug(s)
        if norm and norm not in cleaned:
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
    settings = get_settings()

    title = existing_title or title_from_slug(slug)
    body = existing_body or ""
    related_text = ", ".join(related_slugs[:80]) if related_slugs else "(none yet)"

    user = (
        f"Page slug: {slug}\n"
        f"Page title: {title}\n\n"
        f"=== Current body (markdown) ===\n{_truncate(body, MAX_PAGE_CHARS)}\n\n"
        f"=== New item ===\n"
        f"item_id: {item_id}\n"
        f"Title: {item_title}\n"
        f"Summary: {item_summary or '(none)'}\n"
        f"Raw text:\n{_truncate(item_text or '', MAX_ITEM_CHARS)}\n\n"
        f"=== Available wikilink targets ===\n{related_text}\n\n"
        "Produce the full updated markdown for this page. Output ONLY the markdown."
    )

    resp = await claude(
        purpose="patch_page",
        model=settings.claude_synthesis_model,
        max_tokens=4096,
        system=[
            {"type": "text", "text": PATCH_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user}],
    )
    new_body = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
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
    pages, or None when Claude's answer is empty, cut off or refused."""
    settings = get_settings()

    user = (
        f"Deleted item: [item:{item_id}]\n"
        f"Pages that no longer exist: {', '.join(gone_slugs) or '(none)'}\n\n"
        f"=== Page (markdown) ===\n{body}"
    )
    resp = await claude(
        purpose="forget",
        model=settings.claude_synthesis_model,
        max_tokens=16_000,
        system=[{"type": "text", "text": FORGET_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
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


async def embed_page_chunks(body: str) -> list[tuple[str, list[float]]]:
    chunks = chunk_text(body)
    if not chunks:
        return []
    embs = await embed_texts(chunks, input_type="document")
    return list(zip(chunks, embs, strict=True))
