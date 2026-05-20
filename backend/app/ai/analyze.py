from __future__ import annotations

import json
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import get_settings
from app.extractors.base import ExtractedContent

ANALYSIS_SYSTEM = """You are an analyst summarizing a single piece of saved \
web content (a YouTube video, a tweet, an Instagram post, or a web article) \
for a personal knowledge base. The user will later ask natural-language \
questions to find it.

Return STRICT JSON with these keys (no prose, no markdown fence):
{
  "title":           one-line title, ~60 chars max,
  "summary":         2-3 sentence summary in plain English,
  "tags":            5-10 short topical tags (lowercase, hyphenated),
  "people":          named people mentioned (proper names, full name preferred),
  "handles":         social-media handles or @usernames,
  "brands_products": brands, products, companies, tools, or projects,
  "topics":          2-5 broad topic categories
}

Rules:
- Every array is a JSON array of strings, possibly empty.
- Be specific. Prefer "rust-async-runtimes" over "programming".
- Extract real entities only. Do not invent.
- If content is sparse, still produce best-guess values from any signal.
"""

MAX_TEXT_CHARS = 24_000  # ~6k tokens; leaves headroom for system + image


@dataclass
class Analysis:
    title: str | None
    summary: str | None
    tags: list[str]
    people: list[str]
    handles: list[str]
    brands_products: list[str]
    topics: list[str]


def _truncate(text: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25):]
    return f"{head}\n\n[...truncated...]\n\n{tail}"


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


async def analyze(
    content: ExtractedContent,
    url: str,
    source: str,
) -> Analysis:
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    user_blocks: list[dict] = []

    if image_b64 := content.extra.get("image_b64"):
        user_blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": content.extra.get("image_mime", "image/jpeg"),
                    "data": image_b64,
                },
            }
        )

    body_text = _truncate(content.raw_text)
    prompt = (
        f"URL: {url}\n"
        f"Source: {source}\n"
        f"Site: {content.site_name or ''}\n"
        f"Author: {content.author or ''}\n\n"
        f"Content:\n{body_text or '(no body extracted)'}\n\n"
        "Return ONLY the JSON object as specified."
    )
    user_blocks.append({"type": "text", "text": prompt})

    resp = await client.messages.create(
        model=settings.claude_analysis_model,
        max_tokens=1024,
        system=[{"type": "text", "text": ANALYSIS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_blocks}],
    )

    raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    raw = _strip_json_fence(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Analysis(
            title=content.title,
            summary=None,
            tags=[],
            people=[],
            handles=[],
            brands_products=[],
            topics=[],
        )

    def _strs(key: str) -> list[str]:
        val = data.get(key) or []
        return [str(x).strip() for x in val if isinstance(x, str | int | float) and str(x).strip()]

    return Analysis(
        title=(data.get("title") or content.title or None),
        summary=data.get("summary") or None,
        tags=_strs("tags"),
        people=_strs("people"),
        handles=_strs("handles"),
        brands_products=_strs("brands_products"),
        topics=_strs("topics"),
    )
