from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.extractors.base import ExtractedContent
from app.extractors.webpage import TIMEOUT, USER_AGENT, public_client

MAX_COMMENTS = 10
MAX_COMMENT_CHARS = 600
MAX_LISTING_TITLES = 25


class RedditExtractor:
    """Use Reddit's public JSON API by appending `.json` to the post URL.

    Handles three URL shapes:
      - Comment threads (`/r/<sub>/comments/<id>/...`) → title, selftext,
        link URL, and top comments.
      - Subreddit / user feeds (`/r/<sub>/`, `/u/<user>/`) → list of post
        titles in the current view.
      - `redd.it/<id>` short links → rewritten to the canonical comments URL.

    No auth required for public content. On failure (private sub, deleted
    post, rate limit) we fall back to a stub so the link is still saved.
    """

    source = "reddit"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        try:
            async with public_client(
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
                follow_redirects=True,
            ) as client:
                # iOS share links (`/r/<sub>/s/<id>`) redirect to the canonical
                # `/comments/<id>/<slug>/` URL — resolve that first, otherwise
                # appending `.json` to the share path 307s back to the
                # subreddit root and we lose the post.
                resolved_url = await _resolve_share_link(client, url)
                api_url = _to_json_url(resolved_url)
                resp = await client.get(api_url)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return ExtractedContent(
                raw_text=share_text or url,
                site_name="Reddit",
            )

        return _parse(data, share_text)


async def _resolve_share_link(client: httpx.AsyncClient, url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    # Pattern: ["r", "<sub>", "s", "<id>"]
    if len(parts) >= 4 and parts[0] == "r" and parts[2] == "s":
        resp = await client.get(url)
        return str(resp.url)
    return url


def _to_json_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")

    # redd.it short links don't accept `.json` on their own path — the redirect
    # target drops it. Rebuild as the canonical comments URL on www.reddit.com.
    if host == "redd.it":
        post_id = parsed.path.strip("/").split("/", 1)[0]
        return f"https://www.reddit.com/comments/{post_id}.json?raw_json=1"

    netloc = "www.reddit.com"
    path = parsed.path or "/"
    if not path.endswith(".json"):
        path = path.rstrip("/") + ".json"
    query = "raw_json=1" if not parsed.query else parsed.query + "&raw_json=1"
    return urlunparse(parsed._replace(scheme="https", netloc=netloc, path=path, query=query))


def _parse(data: Any, share_text: str | None) -> ExtractedContent:
    # Comment-thread endpoints return [post_listing, comments_listing].
    if isinstance(data, list) and data:
        post = _first_child(data[0])
        comments = _comment_children(data[1]) if len(data) > 1 else []
        if post and post.get("kind") == "t3":
            return _post_to_content(post.get("data", {}), comments, share_text)

    # Subreddit / user feeds return a single Listing.
    if isinstance(data, dict):
        children = data.get("data", {}).get("children", [])
        titles = [
            c["data"].get("title")
            for c in children
            if c.get("kind") == "t3" and c.get("data", {}).get("title")
        ]
        if titles:
            body = "\n".join(f"- {t}" for t in titles[:MAX_LISTING_TITLES])
            parts = ["Subreddit posts:", body]
            if share_text:
                parts.append("User note:\n" + share_text)
            return ExtractedContent(
                site_name="Reddit",
                raw_text="\n\n".join(parts),
            )

    return ExtractedContent(site_name="Reddit", raw_text=share_text)


def _first_child(listing: Any) -> dict[str, Any] | None:
    if not isinstance(listing, dict):
        return None
    children = listing.get("data", {}).get("children", [])
    return children[0] if children else None


def _comment_children(listing: Any) -> list[dict[str, Any]]:
    if not isinstance(listing, dict):
        return []
    children = listing.get("data", {}).get("children", [])
    return [c for c in children if c.get("kind") == "t1"]


def _post_to_content(
    post: dict[str, Any],
    comment_children: list[dict[str, Any]],
    share_text: str | None,
) -> ExtractedContent:
    title = post.get("title")
    author = post.get("author")
    subreddit = post.get("subreddit")
    selftext = (post.get("selftext") or "").strip()
    link_url = post.get("url_overridden_by_dest") or post.get("url")
    is_self = post.get("is_self", False)

    thumb = post.get("thumbnail")
    if thumb in {None, "", "self", "default", "nsfw", "spoiler", "image"}:
        thumb = None

    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    if subreddit:
        parts.append(f"Subreddit: r/{subreddit}")
    if author and author != "[deleted]":
        parts.append(f"Author: u/{author}")
    if selftext:
        parts.append("Body:\n" + selftext)
    elif link_url and not is_self:
        parts.append(f"Link: {link_url}")

    top_comments = _format_comments(comment_children)
    if top_comments:
        parts.append("Top comments:\n" + top_comments)
    if share_text:
        parts.append("User note:\n" + share_text)

    return ExtractedContent(
        title=title,
        author=author if author and author != "[deleted]" else None,
        site_name=f"r/{subreddit}" if subreddit else "Reddit",
        thumbnail_url=thumb,
        raw_text="\n\n".join(parts) or None,
    )


def _format_comments(children: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for c in children[:MAX_COMMENTS]:
        d = c.get("data", {})
        body = (d.get("body") or "").strip()
        author = d.get("author") or "anon"
        if not body or body in {"[deleted]", "[removed]"}:
            continue
        if len(body) > MAX_COMMENT_CHARS:
            body = body[:MAX_COMMENT_CHARS].rstrip() + "…"
        lines.append(f"- u/{author}: {body}")
    return "\n".join(lines)
