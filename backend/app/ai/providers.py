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
from typing import Any, Protocol

from app.ai.analyze import Analysis, parse, schema
from app.ai.clients import claude_json
from app.config import get_settings

VISION_SYSTEM = """You are an analyst extracting structured information from \
a single document or image that the user saved to a personal knowledge base.

For PDFs: read the document, transcribe its text in `raw_text`, and summarize it.
For images: describe what is visible (people, objects, diagrams, text, scene). \
If the image contains readable text, transcribe it verbatim in `raw_text`.

Fill in:
- title: one-line title, ~60 chars max
- summary: 2-3 sentence summary in plain English
- raw_text: the text itself, verbatim and in order, not a summary of it: \
every page of a PDF (its headings, body, captions and references; skip page \
numbers and running headers), all readable text in an image. For a long \
document, stop after about 60,000 characters.
- tags: 5-10 short topical tags (lowercase, hyphenated)
- people: named people mentioned
- handles: social-media handles or @usernames
- brands_products: brands, products, companies, tools, projects
- topics: 2-5 broad topic categories

Rules:
- Any list may be empty.
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


# raw_text holds a PDF's whole text, so its answer can run long.
_SCHEMA = schema(raw_text={"type": "string"})


def _result(data: dict[str, Any], fallback_title: str | None) -> VisionResult:
    return VisionResult(parse(data, fallback_title), raw_text=data.get("raw_text") or None)


class AnthropicVisionProvider:
    """Vision/PDF analyzer backed by the Anthropic Messages API."""

    name = "anthropic"

    async def analyze_pdf(self, data: bytes, filename: str) -> VisionResult:
        settings = get_settings()
        b64 = base64.b64encode(data).decode("ascii")
        prompt = f"Filename: {filename}\n\nRead the attached PDF."
        fields = await claude_json(
            purpose="analyze_pdf",
            schema=_SCHEMA,
            effort="medium",
            model=settings.claude_analysis_model,
            # 60,000 characters of raw_text plus thinking: room for scripts
            # that take more tokens per character than English (Devanagari).
            max_tokens=64_000,
            system=VISION_SYSTEM,
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
        return _result(fields, fallback_title=filename)

    async def analyze_image(self, data: bytes, mime_type: str) -> VisionResult:
        settings = get_settings()
        b64 = base64.b64encode(data).decode("ascii")
        prompt = "Describe the attached image."
        fields = await claude_json(
            purpose="analyze_image",
            schema=_SCHEMA,
            effort="medium",
            model=settings.claude_analysis_model,
            max_tokens=8000,
            system=VISION_SYSTEM,
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
        return _result(fields, fallback_title=None)


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
