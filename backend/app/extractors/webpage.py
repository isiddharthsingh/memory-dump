from __future__ import annotations

import asyncio

import httpx

from app.extractors.base import ExtractedContent

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class WebpageExtractor:
    source = "web"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        meta = self._parse_og(html)
        body = await asyncio.to_thread(self._extract_body, html, url)

        title = meta.get("og:title") or meta.get("twitter:title")
        thumb = meta.get("og:image") or meta.get("twitter:image")
        site = meta.get("og:site_name")

        text_parts: list[str] = []
        if title:
            text_parts.append(f"Title: {title}")
        if meta.get("og:description"):
            text_parts.append("Summary: " + meta["og:description"])
        if body:
            text_parts.append("Body:\n" + body)
        if share_text:
            text_parts.append("User note:\n" + share_text)

        return ExtractedContent(
            title=title,
            thumbnail_url=thumb,
            site_name=site,
            raw_text="\n\n".join(text_parts) or None,
        )

    @staticmethod
    def _parse_og(html: str) -> dict[str, str]:
        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            return {}
        tree = HTMLParser(html)
        out: dict[str, str] = {}
        for node in tree.css("meta"):
            attrs = node.attributes
            key = attrs.get("property") or attrs.get("name")
            content = attrs.get("content")
            if key and content:
                out[key] = content
        if not out.get("og:title"):
            title_node = tree.css_first("title")
            if title_node and title_node.text():
                out["og:title"] = title_node.text().strip()
        return out

    @staticmethod
    def _extract_body(html: str, url: str) -> str | None:
        try:
            import trafilatura

            text = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
            )
            return text.strip() if text else None
        except Exception:
            return None
