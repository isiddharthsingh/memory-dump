from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.extractors.base import ExtractedContent, detect_source, flatten, get_extractor
from app.extractors.webpage import TIMEOUT, USER_AGENT, public_client

log = logging.getLogger(__name__)

# FxTwitter (the open-source FxEmbed project) reads public posts without auth
# and returns what X's own endpoints leave out: the full text of long posts,
# quoted posts, X Articles and expanded links. X's oEmbed is the fallback.
FXTWITTER = "https://api.fxtwitter.com/i/status/"
OEMBED = "https://publish.x.com/oembed"

_STATUS_ID = re.compile(r"/status(?:es)?/(\d+)")
_X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "t.co", "pic.x.com", "pic.twitter.com"}
_BLANK_LINES = re.compile(r"\n\s*\n+")


class TwitterExtractor:
    """A post's text, the post it quotes, and the article behind it.

    An X Article's body comes with the post. For a post that links out, the
    linked page is read by its own source's extractor, so the item holds the
    article (or transcript) and not just the line that shared it.
    """

    source = "twitter"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        async with public_client(
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True
        ) as client:
            tweet = await _fetch_fxtwitter(client, url)
            if tweet is None:
                return await _from_oembed(client, url, share_text)

        linked: ExtractedContent | None = None
        link = None if tweet.get("article") else linked_url(tweet)
        if link:
            try:
                # Its own source's extractor: a YouTube link gets the transcript.
                # Like every fetch, it only reaches public addresses.
                linked = await get_extractor(detect_source(link)).extract(link)
            except Exception as exc:
                log.info("linked page fetch failed for %s: %s", link, exc)
        return compose(tweet, share_text, link=link, linked=linked)


async def _fetch_fxtwitter(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    m = _STATUS_ID.search(urlparse(url).path)
    if not m:
        return None
    try:
        resp = await client.get(FXTWITTER + m.group(1))
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("fxtwitter failed for %s: %s", url, exc)
        return None
    if not isinstance(data, dict):
        log.warning("fxtwitter sent no JSON object for %s", url)
        return None
    tweet = data.get("tweet") if data.get("code") == 200 else None
    if not tweet:
        log.info("fxtwitter has no post for %s: %s", url, data.get("message"))
    return tweet


def compose(
    tweet: dict[str, Any],
    share_text: str | None,
    *,
    link: str | None = None,
    linked: ExtractedContent | None = None,
) -> ExtractedContent:
    """Sections in the `Key:` layout the app's reader parses. Values keep
    single newlines only: the reader splits sections at blank lines."""
    author = tweet.get("author") or {}
    name = author.get("name") or author.get("screen_name")
    article = tweet.get("article")
    parts: list[str] = []
    if name:
        parts.append(f"Author: {name}")
    if text := flatten(tweet.get("text")):
        parts.append("Tweet:\n" + text)
    if quoted := _quoted(tweet.get("quote")):
        parts.append("Quoted post:\n" + quoted)
    if media := _media_line(tweet):
        parts.append("Media: " + media)
    if article:
        if title := article.get("title"):
            parts.append(f"Title: {title}")
        if body := article_markdown(article) or flatten(article.get("preview_text")):
            parts.append("Body:\n" + body)
    elif link and linked and linked.raw_text and not linked.raw_text.startswith("URL:"):
        # Anything but the stub a bot-walled page leaves.
        parts.append(f"Link: {link}")
        parts.append(linked.raw_text)
    elif card := tweet.get("card"):
        summary = " — ".join(s for s in (card.get("title"), card.get("description")) if s)
        if summary:
            parts.append(f"Link: {card.get('url')}\n{summary}")
    if share_text:
        parts.append("User note:\n" + share_text)

    return ExtractedContent(
        title=(article or {}).get("title") or (f"Post by {name}" if name else "Post on X"),
        author=name,
        site_name="X",
        thumbnail_url=_thumbnail(tweet) or (linked.thumbnail_url if linked else None),
        raw_text="\n\n".join(parts) or None,
    )


def linked_url(tweet: dict[str, Any]) -> str | None:
    """The first page the post links to outside X, if any."""
    facets = (tweet.get("raw_text") or {}).get("facets") or []
    candidates = [f.get("replacement") for f in facets if f.get("type") == "url"]
    candidates.append((tweet.get("card") or {}).get("url"))
    for candidate in candidates:
        if not candidate:
            continue
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme in {"http", "https"} and host and host not in _X_HOSTS:
            return candidate
    return None


def article_markdown(article: dict[str, Any]) -> str:
    """An X Article's Draft.js blocks as markdown, one block per line."""
    content = article.get("content") or {}
    entities = {str(e.get("key")): e.get("value") or {} for e in content.get("entityMap") or []}
    prefixes = {
        "header-one": "# ",
        "header-two": "## ",
        "header-three": "### ",
        "unordered-list-item": "- ",
        "blockquote": "> ",
    }
    lines: list[str] = []
    number = 0
    for block in content.get("blocks") or []:
        kind = block.get("type")
        number = number + 1 if kind == "ordered-list-item" else 0
        if kind == "atomic":
            # Media and embedded posts carry no text; code arrives as a MARKDOWN entity.
            for r in block.get("entityRanges") or []:
                entity = entities.get(str(r.get("key"))) or {}
                if entity.get("type") == "MARKDOWN":
                    code = _BLANK_LINES.sub("\n", (entity.get("data") or {}).get("markdown") or "")
                    if code.strip():
                        lines.append(code.strip())
            continue
        text = flatten(block.get("text"))
        if not text:
            continue
        prefix = f"{number}. " if kind == "ordered-list-item" else prefixes.get(kind, "")
        lines.append(prefix + text)
    return "\n".join(lines)


def _quoted(quote: dict[str, Any] | None) -> str | None:
    if not quote or quote.get("type") == "tombstone":
        return None
    text = flatten(quote.get("text"))
    title = (quote.get("article") or {}).get("title")
    body = text or title
    if not body:
        return None
    handle = (quote.get("author") or {}).get("screen_name")
    return f"@{handle}: {body}" if handle else body


def _media_line(tweet: dict[str, Any]) -> str | None:
    items = (tweet.get("media") or {}).get("all") or []
    if not items:
        return None
    videos = sum(1 for m in items if m.get("type") in {"video", "gif"})
    photos = len(items) - videos
    counts = [
        f"{n} {kind}{'s' if n > 1 else ''}"
        for n, kind in ((videos, "video"), (photos, "photo"))
        if n
    ]
    alts = [m["altText"] for m in items if m.get("altText")]
    return ", ".join(counts) + (". " + " ".join(alts) if alts else "")


def _thumbnail(tweet: dict[str, Any]) -> str | None:
    cover = ((tweet.get("article") or {}).get("cover_media") or {}).get("media_info") or {}
    if cover.get("original_img_url"):
        return cover["original_img_url"]
    for m in (tweet.get("media") or {}).get("all") or []:
        if m.get("type") == "photo" and m.get("url"):
            return m["url"]
        if m.get("thumbnail_url"):
            return m["thumbnail_url"]
    return ((tweet.get("card") or {}).get("image") or {}).get("url")


async def _from_oembed(
    client: httpx.AsyncClient, url: str, share_text: str | None
) -> ExtractedContent:
    """Text and author only. Tweets X won't embed (deleted, private) keep the
    shared text, or the bare link."""
    try:
        resp = await client.get(OEMBED, params={"url": url, "omit_script": "true", "dnt": "true"})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return ExtractedContent(raw_text=share_text or url, site_name="X")

    author = data.get("author_name")
    text = _oembed_text(data.get("html") or "")
    parts: list[str] = []
    if author:
        parts.append(f"Author: {author}")
    if text:
        parts.append("Tweet:\n" + text)
    if share_text:
        parts.append("User note:\n" + share_text)
    return ExtractedContent(
        title=f"Post by {author}" if author else "Post on X",
        author=author,
        site_name="X",
        raw_text="\n\n".join(parts) or None,
    )


def _oembed_text(html: str) -> str:
    """The post's own paragraph, without the `— Name (@handle) date` byline."""
    from selectolax.lexbor import LexborHTMLParser as HTMLParser

    tree = HTMLParser(re.sub(r"<br\s*/?>", "\n", html))
    node = tree.css_first("blockquote p") or tree.body
    return flatten(node.text(separator="") if node else "")
