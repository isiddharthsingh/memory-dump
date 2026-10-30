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
    # Base64 JPEGs taken through a video, for the analysis to look at.
    frames: list[str] = field(default_factory=list)


def flatten(text: str | None) -> str:
    """`text` without blank lines, so it stays one section of raw_text: the
    app's reader splits sections at them. Line by line: a regex over the
    whitespace takes quadratic time on a long run of it."""
    lines = (line.rstrip() for line in (text or "").splitlines())
    return "\n".join(line for line in lines if line).strip()


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
    if host in {"reddit.com", "old.reddit.com", "m.reddit.com", "np.reddit.com", "redd.it"}:
        return "reddit"
    return "web"


def get_extractor(source: str) -> Extractor:
    from app.extractors.instagram import InstagramExtractor
    from app.extractors.reddit import RedditExtractor
    from app.extractors.twitter import TwitterExtractor
    from app.extractors.webpage import WebpageExtractor
    from app.extractors.youtube import YouTubeExtractor

    mapping: dict[str, Extractor] = {
        "youtube": YouTubeExtractor(),
        "twitter": TwitterExtractor(),
        "instagram": InstagramExtractor(),
        "reddit": RedditExtractor(),
        "web": WebpageExtractor(),
    }
    return mapping.get(source, WebpageExtractor())
