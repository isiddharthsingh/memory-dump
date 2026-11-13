from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.clients import claude_json
from app.config import get_settings
from app.extractors.base import ExtractedContent, flatten

# What a save is for. The Library lists items by it, and each kind has its
# own details: a place's addresses, a recipe's ingredients and steps, a
# product's price.
KINDS = ("place", "recipe", "product", "media", "howto", "other")
MAX_PLACES = 10
MAX_STEPS = 12

# The lines of a "Fill in" list that ask for the kind and its details, shared
# by every prompt that analyses a save.
KIND_RULES = """\
- kind: what the user most likely saved it for, one of "place" (a restaurant, \
café, bar, shop, hotel, sight, trail or anywhere else to go), "recipe" (a \
dish or drink to make), "product" (something to buy), "media" (a film, \
series, book, podcast, album or game to watch, read, hear or play), "howto" \
(a tutorial, routine, workout or tip to try), "other" (everything else: \
news, opinion, reference, a thread, a joke). When unsure, "other".
- places: for a "place", every place it names, at most 10, each with its \
name and its address as far as it is given (street, neighbourhood, city, \
country; "" when none is). Empty for any other kind.
- ingredients: for a "recipe", its ingredients with their amounts, one per \
entry. Empty otherwise.
- steps: for a "recipe" or a "howto", its steps in order, one short sentence \
each, at most 12. Empty otherwise.
- price: for a "product", its price as stated, like "$49"; "" otherwise.
"""

_ANALYSIS_SYSTEM = """You are an analyst summarizing a single piece of saved \
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
{kind_rules}
Rules:
- Any list may be empty.
- Be specific. Prefer "rust-async-runtimes" over "programming".
- Extract real entities only. Do not invent.
- If content is sparse, still produce best-guess values from any signal.
"""

ANALYSIS_SYSTEM = _ANALYSIS_SYSTEM.format(kind_rules=KIND_RULES)

CLASSIFY_SYSTEM = f"""You sort one item of a personal library of saved \
things (videos, posts, articles, PDFs, images) by what it was saved for, and \
pull out the details that kind has.

Fill in:
{KIND_RULES}
Extract only what the item states. Do not invent.
"""

MAX_TEXT_CHARS = 24_000  # ~6k tokens; leaves headroom for system + image

# Asked only when there are frames, so other saves keep the system prompt's keys.
FRAMES_NOTE = """The {count} images above are frames taken evenly through the \
video. Also fill in "on_screen": the text the frames show that the \
transcript doesn't say (names, @handles, titles, numbers, code, labels of \
charts and lists), as plain lines; "" when there is none.
"""

_STRINGS = {"type": "array", "items": {"type": "string"}}
_KIND_FIELDS: dict[str, Any] = {
    "kind": {"type": "string", "enum": list(KINDS)},
    "places": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "address": {"type": "string"}},
            "required": ["name", "address"],
            "additionalProperties": False,
        },
    },
    "ingredients": _STRINGS,
    "steps": _STRINGS,
    "price": {"type": "string"},
}
_FIELDS: dict[str, Any] = {
    "title": {"type": "string"},
    "summary": {"type": "string"},
    "tags": _STRINGS,
    "people": _STRINGS,
    "handles": _STRINGS,
    "brands_products": _STRINGS,
    "topics": _STRINGS,
    **_KIND_FIELDS,
}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def schema(**extra: dict[str, Any]) -> dict[str, Any]:
    """The JSON schema an analysis comes back in, plus `extra` string fields."""
    return _object(_FIELDS | extra)


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
    # One of KINDS, and that kind's details (None when it has none).
    kind: str | None = None
    details: dict[str, Any] | None = None


def _truncate(text: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25):]
    return f"{head}\n\n[...truncated...]\n\n{tail}"


def read_kind(data: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """The kind Claude chose and the details that kind keeps; whatever it
    filled in for another kind is dropped."""

    def strs(key: str) -> list[str]:
        return [x.strip() for x in data.get(key) or [] if isinstance(x, str) and x.strip()]

    kind = data.get("kind")
    if kind not in KINDS:
        kind = "other"
    details: dict[str, Any] = {}
    if kind == "place":
        # Each place once: the app tells a save's places apart by name and address.
        places = dict.fromkeys(
            (p["name"].strip(), str(p.get("address") or "").strip())
            for p in data.get("places") or []
            if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip()
        )
        details["places"] = [
            {"name": name, "address": address} for name, address in list(places)[:MAX_PLACES]
        ]
    if kind == "recipe":
        details["ingredients"] = strs("ingredients")
    if kind in ("recipe", "howto"):
        details["steps"] = strs("steps")[:MAX_STEPS]
    if kind == "product" and isinstance(data.get("price"), str):
        details["price"] = data["price"].strip()
    details = {key: value for key, value in details.items() if value}
    return kind, details or None


def parse(data: dict[str, Any], fallback_title: str | None) -> Analysis:
    """An Analysis from the fields Claude filled in."""

    def strs(key: str) -> list[str]:
        return [x.strip() for x in data.get(key) or [] if x.strip()]

    kind, details = read_kind(data)
    return Analysis(
        title=data.get("title") or fallback_title or None,
        summary=data.get("summary") or None,
        tags=strs("tags"),
        people=strs("people"),
        handles=strs("handles"),
        brands_products=strs("brands_products"),
        topics=strs("topics"),
        kind=kind,
        details=details,
    )


async def classify(
    *, title: str | None, summary: str | None, raw_text: str | None, url: str, source: str
) -> tuple[str, dict[str, Any] | None]:
    """The kind and details of an item analysed before kinds were kept, from
    what is saved of it (app/workers/classify.py)."""
    prompt = (
        f"URL: {url}\n"
        f"Source: {source}\n"
        f"Title: {title or ''}\n"
        f"Summary: {summary or ''}\n\n"
        f"Content:\n{_truncate(raw_text) or '(no text saved)'}"
    )
    data = await claude_json(
        purpose="classify",
        schema=_object(_KIND_FIELDS),
        effort="medium",
        model=get_settings().claude_analysis_model,
        max_tokens=8000,
        system=CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return read_kind(data)


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
