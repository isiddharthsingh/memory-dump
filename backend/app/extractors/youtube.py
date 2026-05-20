from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlparse

from app.extractors.base import ExtractedContent


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/")
        return candidate if VIDEO_ID_RE.match(candidate) else None
    if host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            v = parse_qs(parsed.query).get("v", [None])[0]
            if v and VIDEO_ID_RE.match(v):
                return v
        if parsed.path.startswith(("/embed/", "/shorts/")):
            candidate = parsed.path.split("/")[2]
            return candidate if VIDEO_ID_RE.match(candidate) else None
    return None


class YouTubeExtractor:
    source = "youtube"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        video_id = extract_video_id(url)
        if not video_id:
            return ExtractedContent(raw_text=share_text or url)

        metadata, transcript = await asyncio.gather(
            asyncio.to_thread(self._fetch_metadata, url),
            asyncio.to_thread(self._fetch_transcript, video_id),
        )

        title = metadata.get("title")
        author = metadata.get("uploader") or metadata.get("channel")
        description = metadata.get("description") or ""
        thumbnail = metadata.get("thumbnail")

        text_parts: list[str] = []
        if title:
            text_parts.append(f"Title: {title}")
        if author:
            text_parts.append(f"Channel: {author}")
        if transcript:
            text_parts.append("Transcript:\n" + transcript)
        elif description:
            text_parts.append("Description:\n" + description)
        if share_text:
            text_parts.append("User note:\n" + share_text)

        return ExtractedContent(
            title=title,
            author=author,
            thumbnail_url=thumbnail,
            site_name="YouTube",
            raw_text="\n\n".join(p for p in text_parts if p) or None,
            extra={
                "video_id": video_id,
                "duration": str(metadata.get("duration") or ""),
            },
        )

    @staticmethod
    def _fetch_metadata(url: str) -> dict:
        try:
            from yt_dlp import YoutubeDL

            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": False,
                "noplaylist": True,
            }
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info or {}
        except Exception:
            return {}

    @staticmethod
    def _fetch_transcript(video_id: str) -> str | None:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            data = YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
            return " ".join(seg["text"] for seg in data).strip() or None
        except Exception:
            return None
