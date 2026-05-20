from __future__ import annotations

import httpx

from app.extractors.base import ExtractedContent
from app.extractors.webpage import TIMEOUT, USER_AGENT


class TwitterExtractor:
    """Use publish.twitter.com oEmbed for public tweets.

    No auth required; returns HTML with the tweet text. If the endpoint
    returns 4xx (tweet deleted, private, etc.) we fall back to whatever
    text the share-sheet passed in `share_text`.
    """

    source = "twitter"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        oembed = f"https://publish.twitter.com/oembed?url={url}&omit_script=true&dnt=true"
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            ) as client:
                resp = await client.get(oembed)
                if resp.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "oembed failed", request=resp.request, response=resp
                    )
                data = resp.json()
        except Exception:
            return ExtractedContent(raw_text=share_text or url, site_name="X")

        author = data.get("author_name")
        html = data.get("html") or ""
        text = self._html_to_text(html)

        parts: list[str] = []
        if author:
            parts.append(f"Author: {author}")
        if text:
            parts.append("Tweet:\n" + text)
        if share_text:
            parts.append("User note:\n" + share_text)

        return ExtractedContent(
            title=f"Tweet by {author}" if author else "Tweet",
            author=author,
            site_name="X",
            raw_text="\n\n".join(parts) or None,
        )

    @staticmethod
    def _html_to_text(html: str) -> str:
        try:
            from selectolax.parser import HTMLParser

            tree = HTMLParser(html)
            text = tree.text(separator=" ", strip=True)
            return " ".join(text.split())
        except Exception:
            return html
