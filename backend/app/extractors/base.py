from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse


@dataclass
class ExtractedContent:
    """Normalized representation of a fetched URL."""

    title: str | None = None
    raw_text: str | None = None
    thumbnail_url: str | None = None
    author: str | None = None
    site_name: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


class Extractor(Protocol):
    source: str

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent: ...


def detect_source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube"
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return "twitter"
    if host == "instagram.com":
        return "instagram"
    return "web"


def get_extractor(source: str) -> Extractor:
    from app.extractors.instagram import InstagramExtractor
    from app.extractors.twitter import TwitterExtractor
    from app.extractors.webpage import WebpageExtractor
    from app.extractors.youtube import YouTubeExtractor

    mapping: dict[str, Extractor] = {
        "youtube": YouTubeExtractor(),
        "twitter": TwitterExtractor(),
        "instagram": InstagramExtractor(),
        "web": WebpageExtractor(),
    }
    return mapping.get(source, WebpageExtractor())
