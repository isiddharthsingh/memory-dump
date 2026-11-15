"""Google Maps links. The address a shared link leads to names the place:
`/maps?q=<name>,+<address>` or `/maps/place/<name>/@…`. The page there is
about the place only for some clients and some links; otherwise it is
Google's own, in the link's language, with a map of somewhere else."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote_plus, urlsplit

from app.extractors.base import ExtractedContent, flatten

_GOOGLE = re.compile(r"^(www\.|maps\.)?google\.(com|[a-z]{2}|com?\.[a-z]{2})$")
# A point on the map, not a place's name.
_COORDINATES = re.compile(r"^(loc:)?\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$")


def is_maps_link(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "maps.app.goo.gl":
        return True
    if host == "goo.gl":
        return parts.path.startswith("/maps/")
    if not _GOOGLE.match(host):
        return False
    return host.startswith("maps.") or parts.path == "/maps" or parts.path.startswith("/maps/")


def place_text(url: str) -> str | None:
    """The place a Maps address names, as Google wrote it ("name, address"),
    or None when it names none: a short link not yet followed, a point, a
    view, a route, a place by its id."""
    if not is_maps_link(url):
        return None
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    segments = parts.path.split("/")
    if segments[1:3] == ["maps", "place"] and len(segments) > 3:
        text = unquote_plus(segments[3])
    else:
        text = (query.get("q") or query.get("query") or [""])[0]
    text = " ".join(text.split())
    return text if text and not _COORDINATES.match(text) else None


def page(url: str, meta: dict[str, str], share_text: str | None) -> ExtractedContent:
    """What to save of the Maps page at `url` (after redirects), from its meta
    tags `meta`; its body only asks for JavaScript. When the address names a
    place, the page's title, description and picture are kept only if the
    title starts with the place's name: any other page is Google's own,
    whatever language or client it was written for."""
    text = place_text(url)
    title = flatten(meta.get("og:title")).removesuffix(" - Google Maps")
    if text is None:
        about = True
    else:
        name = text.split(",")[0].strip().casefold()
        about = bool(name) and title.casefold().startswith(name)
        if not about:
            title = text
    sections = [f"Title: {title}"] if title else []
    if text is not None:
        sections.append(f"Place: {text}")
    if about and (summary := flatten(meta.get("og:description") or meta.get("description"))):
        sections.append(f"Summary: {summary}")
    if note := flatten(share_text):
        sections.append("User note:\n" + note)
    return ExtractedContent(
        title=title or None,
        thumbnail_url=(meta.get("og:image") or None) if about else None,
        site_name="Google Maps",
        raw_text="\n\n".join(sections) or None,
    )
