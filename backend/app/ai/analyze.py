from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.clients import claude_json
from app.config import get_settings
from app.extractors.base import ExtractedContent, flatten

ANALYSIS_SYSTEM = """You are an analyst summarizing a single piece of saved \
web content (a YouTube video, a tweet, an Instagram post, or a web article) \
for a personal knowledge base. The user will later ask natural-language \
questions to find it.

Fill in:
- title: one-line title, ~60 chars max
- summary: 2-3 sentence summary in plain English
- tags: 5-10 short topical tags (lowercase, hyphenated)
- people: named people mentioned (proper names, full name preferred)
- handles: social-media handles or @usernames
- brands_products: brands, products, companies, tools, or projects
- topics: 2-5 broad topic categories

Rules:
- Any list may be empty.
- Be specific. Prefer "rust-async-runtimes" over "programming".
- Extract real entities only. Do not invent.
- If content is sparse, still produce best-guess values from any signal.
"""

MAX_TEXT_CHARS = 24_000  # ~6k tokens; leaves headroom for system + image

# Asked only when there are frames, so other saves keep the system prompt's keys.
FRAMES_NOTE = """The {count} images above are frames taken evenly through the \
video. Also fill in "on_screen": the text the frames show that the \
transcript doesn't say (names, @handles, titles, numbers, code, labels of \
charts and lists), as plain lines; "" when there is none.
"""

_STRINGS = {"type": "array", "items": {"type": "string"}}
_FIELDS: dict[str, Any] = {
    "title": {"type": "string"},
    "summary": {"type": "string"},
    "tags": _STRINGS,
    "people": _STRINGS,
    "handles": _STRINGS,
    "brands_products": _STRINGS,
    "topics": _STRINGS,
}


def schema(**extra: dict[str, Any]) -> dict[str, Any]:
    """The JSON schema an analysis comes back in, plus `extra` string fields."""
    properties = _FIELDS | extra
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@dataclass
class Analysis:
    title: str | None
    summary: str | None
    tags: list[str]
    people: list[str]
    handles: list[str]
    brands_products: list[str]
    topics: list[str]
    # Text a video's frames show, kept with its raw text so search finds it.
    on_screen: str | None = None


def _truncate(text: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25):]
    return f"{head}\n\n[...truncated...]\n\n{tail}"


def parse(data: dict[str, Any], fallback_title: str | None) -> Analysis:
    """An Analysis from the fields Claude filled in."""

    def strs(key: str) -> list[str]:
        return [x.strip() for x in data.get(key) or [] if x.strip()]

    return Analysis(
        title=data.get("title") or fallback_title or None,
        summary=data.get("summary") or None,
        tags=strs("tags"),
        people=strs("people"),
        handles=strs("handles"),
        brands_products=strs("brands_products"),
        topics=strs("topics"),
    )


async def analyze(
    content: ExtractedContent,
    url: str,
    source: str,
) -> Analysis:
    settings = get_settings()

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
    for frame in content.frames:
        user_blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame},
            }
        )

    body_text = _truncate(content.raw_text)
    prompt = (
        f"URL: {url}\n"
        f"Source: {source}\n"
        f"Site: {content.site_name or ''}\n"
        f"Author: {content.author or ''}\n\n"
        f"Content:\n{body_text or '(no body extracted)'}"
        + ("\n\n" + FRAMES_NOTE.format(count=len(content.frames)) if content.frames else "")
    )
    user_blocks.append({"type": "text", "text": prompt})

    extra = {"on_screen": {"type": "string"}} if content.frames else {}
    # A schema guarantees the fields: an answer missing them used to save the
    # item without its summary, tags and the anchors proper-noun search needs.
    data = await claude_json(
        purpose="analyze",
        schema=schema(**extra),
        effort="medium",
        model=settings.claude_analysis_model,
        max_tokens=8000,
        system=ANALYSIS_SYSTEM,
        messages=[{"role": "user", "content": user_blocks}],
    )
    analysis = parse(data, fallback_title=content.title)
    if content.frames:
        analysis.on_screen = flatten(data.get("on_screen")) or None
    return analysis
