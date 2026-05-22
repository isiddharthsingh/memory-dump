"""Vision/PDF analysis providers.

A thin abstraction over multimodal model calls used by the file-ingest
pipeline. The shape is intentionally narrow so a second provider
(OpenRouter, OpenAI, local model, ...) can be added by implementing
`VisionProvider` and registering it in `get_vision_provider`.

The existing URL-ingest path still uses `app.ai.analyze.analyze` directly
against Anthropic; this module covers only the new file-based path.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Protocol

from anthropic import AsyncAnthropic

from app.ai.analyze import Analysis
from app.config import get_settings

VISION_SYSTEM = """You are an analyst extracting structured information from \
a single document or image that the user saved to a personal knowledge base.

For PDFs: read the document, transcribe its meaningful text, and summarize it.
For images: describe what is visible (people, objects, diagrams, text, scene). \
If the image contains readable text, transcribe it verbatim in `raw_text`.

Return STRICT JSON with these keys (no prose, no markdown fence):
{
  "title":           one-line title, ~60 chars max,
  "summary":         2-3 sentence summary in plain English,
  "raw_text":        full extracted/transcribed text (may be long),
  "tags":            5-10 short topical tags (lowercase, hyphenated),
  "people":          named people mentioned,
  "handles":         social-media handles or @usernames,
  "brands_products": brands, products, companies, tools, projects,
  "topics":          2-5 broad topic categories
}

Rules:
- Every array is a JSON array of strings, possibly empty.
- Be specific. Prefer "rust-async-runtimes" over "programming".
- Extract real entities only. Do not invent.
"""


class VisionResult:
    """Pair of (Analysis, raw_text) returned by vision providers."""

    __slots__ = ("analysis", "raw_text")

    def __init__(self, analysis: Analysis, raw_text: str | None) -> None:
        self.analysis = analysis
        self.raw_text = raw_text


class VisionProvider(Protocol):
    """Multimodal analysis interface.

    Implementations receive raw bytes plus a hint (filename or mime type)
    and return a populated `VisionResult`.
    """

    async def analyze_pdf(self, data: bytes, filename: str) -> VisionResult: ...

    async def analyze_image(self, data: bytes, mime_type: str) -> VisionResult: ...


def _strip_json_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _parse(raw: str, fallback_title: str | None) -> VisionResult:
    raw = _strip_json_fence(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return VisionResult(
            Analysis(
                title=fallback_title,
                summary=None,
                tags=[],
                people=[],
                handles=[],
                brands_products=[],
                topics=[],
            ),
            raw_text=None,
        )

    def _strs(key: str) -> list[str]:
        val = data.get(key) or []
        return [str(x).strip() for x in val if isinstance(x, str | int | float) and str(x).strip()]

    analysis = Analysis(
        title=(data.get("title") or fallback_title or None),
        summary=data.get("summary") or None,
        tags=_strs("tags"),
        people=_strs("people"),
        handles=_strs("handles"),
        brands_products=_strs("brands_products"),
        topics=_strs("topics"),
    )
    raw_text = data.get("raw_text")
    if not isinstance(raw_text, str):
        raw_text = None
    return VisionResult(analysis, raw_text=raw_text)


class AnthropicVisionProvider:
    """Vision/PDF analyzer backed by the Anthropic Messages API."""

    name = "anthropic"

    async def analyze_pdf(self, data: bytes, filename: str) -> VisionResult:
        settings = get_settings()
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        b64 = base64.b64encode(data).decode("ascii")
        prompt = (
            f"Filename: {filename}\n\n"
            "Read the attached PDF and return ONLY the JSON object as specified."
        )
        resp = await client.messages.create(
            model=settings.claude_analysis_model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": VISION_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return _parse(text, fallback_title=filename)

    async def analyze_image(self, data: bytes, mime_type: str) -> VisionResult:
        settings = get_settings()
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        b64 = base64.b64encode(data).decode("ascii")
        prompt = "Describe the attached image and return ONLY the JSON object as specified."
        resp = await client.messages.create(
            model=settings.claude_analysis_model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": VISION_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return _parse(text, fallback_title=None)


def get_vision_provider() -> VisionProvider:
    """Pick a vision provider from settings.

    To add a new backend (e.g. OpenRouter): implement `VisionProvider` and
    add another branch here. The name comes from the `AI_VISION_PROVIDER`
    env var.
    """
    name = (get_settings().ai_vision_provider or "anthropic").lower()
    if name == "anthropic":
        return AnthropicVisionProvider()
    # Future:
    #   if name == "openrouter":
    #       from app.ai.providers_openrouter import OpenRouterVisionProvider
    #       return OpenRouterVisionProvider()
    raise ValueError(f"Unknown AI_VISION_PROVIDER: {name!r}")
