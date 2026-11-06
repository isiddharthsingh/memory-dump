from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse, urlsplit, urlunsplit


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
    # The file, when the link is a PDF: Claude reads it as an upload is read,
    # and `title` is its file name until then.
    pdf: bytes | None = None


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


# Query parameters that only record where a link was shared or clicked from,
# besides every `utm_*`: `si` is YouTube's and Spotify's, `share_id` Reddit's,
# `xmt` Threads'.
_TRACKERS = frozenset(
    {
        "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "twclid", "ttclid",
        "igsh", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi", "ref_src",
        "si", "share_id", "xmt",
    }
)  # fmt: skip
# What X adds to any link it shares.
_X_SHARE = frozenset({"a", "s", "t"})
# A post or reel, under its author's name or not; not a reel's sound page, and
# not a `/share/` link, whose code only redirects to the post.
_INSTAGRAM_POST = re.compile(
    r"^/(?!share/)(?:[A-Za-z0-9._]+/)?(p|reels?|tv)/(?!audio/)([A-Za-z0-9_-]+)"
)
_X_STATUS = re.compile(r"^/([A-Za-z0-9_]+)(?:/web)?/status(?:es)?/(\d+)")


def canonical_url(url: str) -> str:
    """The form of `url` an item is saved under, so sharing a saved link again
    finds it: apps add a parameter that changes with every share (Instagram's
    is even named differently each time, `?igsh=`, `?exln=`, `?dlrf=`; X's is
    `?s=12&a=…`), and the fragment never reaches the server. A post on
    Instagram, X or YouTube comes down to its id; any other page keeps all
    of its query but the trackers, as written.

    Saved items keep the form this gave when they were saved, so a change
    here needs a migration that rewrites `items.url` with it, as
    0010_canonical_urls did; otherwise the next share of an older item misses
    it and is analyzed again."""
    parts = urlsplit(url)
    source = detect_source(url)
    if source == "instagram":
        if m := _INSTAGRAM_POST.match(parts.path):
            kind = "reel" if m[1] == "reels" else m[1]
            return f"https://www.instagram.com/{kind}/{m[2]}/"
        return f"https://www.instagram.com{parts.path}"
    if source == "twitter" and (m := _X_STATUS.match(parts.path)):
        # Handles are case-insensitive.
        return f"https://x.com/{m[1].lower()}/status/{m[2]}"
    if source == "youtube":
        from app.extractors.youtube import extract_video_id

        if video_id := extract_video_id(url):
            return f"https://www.youtube.com/watch?v={video_id}"
    query = "&".join(
        param
        for param in parts.query.split("&")
        if param and not _tracker(param.split("=", 1)[0].lower(), source)
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _tracker(name: str, source: str) -> bool:
    return (
        name.startswith("utm_") or name in _TRACKERS or (source == "twitter" and name in _X_SHARE)
    )


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
