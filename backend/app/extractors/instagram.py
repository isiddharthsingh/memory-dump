from __future__ import annotations

import base64

from app.config import get_settings
from app.extractors import reel
from app.extractors.base import ExtractedContent, flatten
from app.extractors.webpage import TIMEOUT, USER_AGENT, public_client


class InstagramExtractor:
    """A reel, or any post with a video, is read by app/extractors/reel.py:
    its caption, frames taken through it and a transcript of what is said, so
    the analysis sees the video and not just its cover.

    Anything else (a photo, or a video Instagram won't hand over) is read
    from the post's page, as Instagram has no clean unauth API:
    1. Fetch the URL and pull og:image / og:title / og:description metadata.
    2. If we got an og:image, download it as base64 so the analyzer can
       send it to Claude vision and produce a textual description.
    3. Combine the share-text (often contains the caption when sharing
       from the IG app) with the vision description and metadata.
    """

    source = "instagram"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        # Two thirds of the extraction's time, leaving the rest for the post's page.
        found = await reel.fetch(url, get_settings().extract_timeout_s * 2 / 3)
        if found is None:
            return await self._from_page(url, share_text)
        return _from_reel(found, share_text)

    async def _from_page(self, url: str, share_text: str | None) -> ExtractedContent:
        async with public_client(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.get(url)
                meta = self._parse_meta(resp.text) if resp.status_code == 200 else {}
            except Exception:
                meta = {}

            image_b64 = None
            image_url = meta.get("og:image")
            if image_url:
                try:
                    img_resp = await client.get(image_url)
                    if img_resp.status_code == 200:
                        image_b64 = base64.b64encode(img_resp.content).decode("ascii")
                except Exception:
                    pass

        title = meta.get("og:title")
        description = meta.get("og:description")

        parts: list[str] = []
        if title:
            parts.append(f"Title: {title}")
        if description:
            parts.append(f"Description: {description}")
        if share_text:
            parts.append("User note / caption:\n" + share_text)

        extra: dict[str, str] = {}
        if image_b64:
            extra["image_b64"] = image_b64
            extra["image_mime"] = "image/jpeg"  # IG og:image is almost always jpeg

        return ExtractedContent(
            title=title or "Instagram post",
            thumbnail_url=image_url,
            site_name="Instagram",
            raw_text="\n\n".join(parts) or None,
            extra=extra,
        )

    @staticmethod
    def _parse_meta(html: str) -> dict[str, str]:
        try:
            from selectolax.lexbor import LexborHTMLParser as HTMLParser
        except ImportError:
            return {}
        out: dict[str, str] = {}
        tree = HTMLParser(html)
        for node in tree.css("meta"):
            attrs = node.attributes
            key = attrs.get("property") or attrs.get("name")
            content = attrs.get("content")
            if key and content:
                out[key] = content
        return out


def _from_reel(found: reel.Reel, share_text: str | None) -> ExtractedContent:
    """Sections in the `Key:` layout the app's reader parses; the frames go to
    the analysis, which sees the video through them."""
    handle = f"@{found.handle}" if found.handle else None
    by = found.author or handle
    author = f"{found.author} ({handle})" if found.author and handle else by
    parts: list[str] = []
    if author:
        parts.append(f"Author: {author}")
    if caption := flatten(found.caption):
        parts.append("Description:\n" + caption)
    if found.transcript:
        parts.append("Transcript:\n" + found.transcript)
    if note := flatten(share_text):
        parts.append("User note:\n" + note)
    return ExtractedContent(
        title=f"Reel by {by}" if by else "Instagram reel",
        author=author,
        site_name="Instagram",
        thumbnail_url=found.thumbnail_url,
        raw_text="\n\n".join(parts) or None,
        frames=found.frames,
    )
