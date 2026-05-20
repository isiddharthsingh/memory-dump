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

from anthropic import AsyncAnthropic
from slugify import slugify

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


def _normalize_slug(s: str) -> str:
    return slugify(s, max_length=80, lowercase=True)


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
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    existing_text = ", ".join(existing_slugs[:300]) if existing_slugs else "(none yet)"
    item_facts = (
        f"Item title: {item_title}\n"
        f"Summary: {item_summary or '(none)'}\n"
        f"Topics: {', '.join(item_topics) or '(none)'}\n"
        f"Tags: {', '.join(item_tags) or '(none)'}\n\n"
        f"Existing slugs: {existing_text}\n\n"
        "Return JSON only."
    )

    resp = await client.messages.create(
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
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    title = existing_title or slug.replace("-", " ").title()
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

    resp = await client.messages.create(
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


async def embed_page_chunks(body: str) -> list[tuple[str, list[float]]]:
    chunks = chunk_text(body)
    if not chunks:
        return []
    embs = await embed_texts(chunks, input_type="document")
    return list(zip(chunks, embs, strict=True))
