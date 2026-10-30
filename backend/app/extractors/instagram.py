from __future__ import annotations

import base64

from app.extractors.base import ExtractedContent
from app.extractors.webpage import TIMEOUT, USER_AGENT, public_client


class InstagramExtractor:
    """Instagram has no clean unauth API.

    Strategy:
    1. Fetch the URL and pull og:image / og:title / og:description metadata.
    2. If we got an og:image, download it as base64 so the analyzer can
       send it to Claude vision and produce a textual description.
    3. Combine the share-text (often contains the caption when sharing
       from the IG app) with the vision description and metadata.
    """

    source = "instagram"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
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
