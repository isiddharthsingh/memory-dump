from __future__ import annotations

import asyncio
import ipaddress
from pathlib import PurePosixPath
from typing import Any

import httpcore
import httpx

from app.config import get_settings
from app.extractors.base import ExtractedContent, flatten

# Tried in order. The Safari UA is the default; if a site walls it off
# (Medium, some Substack tier links, Cloudflare bot challenges), we retry
# as Googlebot — most publishers leave that lane open or they'd lose SEO —
# and finally as facebookexternalhit, which is what social link-preview
# bots send and which sites usually permit for OG/Twitter card scraping.
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
)

# Re-exported for the twitter / instagram extractors that still want a single
# UA string. Points at the primary entry in the chain above.
USER_AGENT = USER_AGENTS[0]

# Status codes that mean "your client is blocked" rather than "the page
# doesn't exist". These trigger a UA retry; anything else (404, 5xx)
# bubbles up so we don't burn the chain.
RETRY_STATUSES = frozenset({401, 403, 429, 451})

# Public read-mode proxy. When every UA gets blocked (typically because the
# publisher filters by datacenter IP, not UA — Medium does this), we fetch
# the article through Jina AI Reader, which returns a markdown rendering
# with a "Title:" / "Markdown Content:" frontmatter.
JINA_READER = "https://r.jina.ai/"

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class TooLarge(Exception):
    """The page's body is over EXTRACT_MAX_BYTES, before or after
    decompression. The app shows the stored `TooLarge: <url>` as its own
    message."""


class NotPublic(Exception):
    """The address isn't on the open internet: this machine, its network, or
    a cloud metadata service. The app shows the stored `NotPublic: <host>`
    as its own message."""


async def public_address(host: str) -> str:
    """An address for `host`, when every address it resolves to is public."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None) if host else []
    except OSError as exc:
        # A name that doesn't resolve fails as it would anywhere else.
        raise httpcore.ConnectError(f"{host}: {exc}") from exc
    addresses = [str(info[4][0]).split("%")[0] for info in infos]
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise NotPublic(host)
    return addresses[0]


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Resolves and checks the host as each connection opens, then connects to
    the address it checked, so a DNS answer can't change in between."""

    def __init__(self) -> None:
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore's interface)
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        address = await public_address(host)
        return await self._backend.connect_tcp(
            address, port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 (httpcore's interface)
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise NotPublic(path)

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """Every connection, redirects included, goes only to public addresses.
    TLS still checks the certificate against the host name."""

    def __init__(self) -> None:
        super().__init__()
        # httpx has no public way to pick httpcore's network backend.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(), network_backend=_PublicOnlyBackend()
        )


class _CappedClient(httpx.AsyncClient):
    """Reads at most `max_bytes` of a response body, counted after
    decompression, so neither a huge download nor a small compressed bomb can
    fill the worker's memory. Raises TooLarge past it. Follows redirects itself:
    httpx would read each redirect's whole body before following it."""

    def __init__(self, *, max_bytes: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._max_bytes = max_bytes

    async def send(  # type: ignore[override]
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        follow_redirects: bool | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        follow = self.follow_redirects if follow_redirects is None else follow_redirects
        for _ in range(self.max_redirects + 1):
            response = await super().send(request, stream=True, follow_redirects=False, **kwargs)
            if not (follow and response.next_request is not None):
                break
            # Its body is never read.
            await response.aclose()
            request = response.next_request
        else:
            raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.", request=request)
        if stream:
            return response
        try:
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > self._max_bytes:
                raise TooLarge(str(request.url))
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    raise TooLarge(str(request.url))
        finally:
            await response.aclose()
        # What Response.aread() would have stored.
        response._content = bytes(body)
        return response


def public_client(**kwargs: Any) -> httpx.AsyncClient:
    """The HTTP client every extractor fetches with. Anyone who can sign up
    picks the URLs, so it only reaches public addresses, no proxy from the
    environment connects for it, and it reads at most EXTRACT_MAX_BYTES of a
    body."""
    return _CappedClient(
        transport=PublicOnlyTransport(),
        trust_env=False,
        max_bytes=get_settings().extract_max_bytes,
        **kwargs,
    )


class WebpageExtractor:
    source = "web"

    async def extract(self, url: str, share_text: str | None = None) -> ExtractedContent:
        async with public_client(timeout=TIMEOUT, follow_redirects=True) as client:
            direct = await _fetch_direct(client, url)
            if direct is not None:
                if is_pdf(direct):
                    return _from_pdf(direct, share_text)
                return await self._from_html(direct.text, url, share_text)
            # Direct fetch hit a wall on every UA. Try the reader fallback.
            return await self._from_reader(client, url, share_text)

    async def _from_html(self, html: str, url: str, share_text: str | None) -> ExtractedContent:
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

    async def _from_reader(
        self,
        client: httpx.AsyncClient,
        url: str,
        share_text: str | None,
    ) -> ExtractedContent:
        # Reader requires no auth for basic use. It rate-limits aggressively,
        # so we only hit it after all direct UAs have failed.
        resp = await client.get(
            JINA_READER + url,
            headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
        )
        resp.raise_for_status()
        text = resp.text

        title, body = _split_reader_payload(text)
        if _looks_like_challenge(title, body):
            # Even the reader proxy hit a bot wall. Save a stub so the user
            # still has the link in their inbox.
            return _stub(url, share_text)

        text_parts: list[str] = []
        if title:
            text_parts.append(f"Title: {title}")
        if body:
            text_parts.append("Body:\n" + body)
        if share_text:
            text_parts.append("User note:\n" + share_text)

        return ExtractedContent(
            title=title,
            thumbnail_url=None,
            site_name=None,
            raw_text="\n\n".join(text_parts) or None,
        )

    @staticmethod
    def _parse_og(html: str) -> dict[str, str]:
        try:
            from selectolax.lexbor import LexborHTMLParser as HTMLParser
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


async def _fetch_direct(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """Try each UA in order. Returns the first 2xx response, None if every UA
    was rate-limited or blocked. Raises on non-block errors (404, 5xx)."""
    blocked = False
    for ua in USER_AGENTS:
        resp = await client.get(url, headers={"User-Agent": ua})
        if resp.status_code in RETRY_STATUSES:
            blocked = True
            continue
        resp.raise_for_status()
        return resp
    if blocked:
        return None
    return None  # all UAs blocked


PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})


def is_pdf(resp: httpx.Response) -> bool:
    """By its Content-Type, or by its first bytes when the server sends a
    generic one (application/octet-stream, text/plain)."""
    media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    return media_type in PDF_TYPES or resp.content[:1024].lstrip().startswith(b"%PDF-")


def _from_pdf(resp: httpx.Response, share_text: str | None) -> ExtractedContent:
    """The PDF itself, for the analysis to read; trafilatura would read its
    bytes as text. The title is the file's name in the URL it came from
    (after redirects) until the analysis names it."""
    # httpx has decoded the path already.
    name = " ".join(PurePosixPath(resp.url.path).name.split())[:120]
    note = flatten(share_text)
    return ExtractedContent(
        title=name or None,
        raw_text="User note:\n" + note if note else None,
        pdf=resp.content,
    )


CHALLENGE_TITLES = ("just a moment", "attention required", "checking your browser")
CHALLENGE_BODY_PHRASES = (
    "performing security verification",
    "checking the site connection security",
    "enable javascript and cookies to continue",
)


def _looks_like_challenge(title: str | None, body: str | None) -> bool:
    if title:
        low = title.lower()
        if any(p in low for p in CHALLENGE_TITLES):
            return True
    if body:
        low = body.lower()
        if any(p in low for p in CHALLENGE_BODY_PHRASES):
            return True
    return False


def _stub(url: str, share_text: str | None) -> ExtractedContent:
    parts = [f"URL: {url}"]
    if share_text:
        parts.append("User note:\n" + share_text)
    return ExtractedContent(
        title=None,
        thumbnail_url=None,
        site_name=None,
        raw_text="\n\n".join(parts),
    )


def _split_reader_payload(text: str) -> tuple[str | None, str | None]:
    """Jina Reader returns 'Title: ...\\nURL Source: ...\\nMarkdown Content:\\n...'.
    Pull the title and the body separately."""
    title: str | None = None
    body: str | None = None
    marker = "\nMarkdown Content:\n"
    head_end = text.find(marker)
    if head_end == -1:
        # No frontmatter — treat the whole payload as body.
        stripped = text.strip()
        return None, stripped or None
    head = text[:head_end]
    body = text[head_end + len(marker) :].strip() or None
    for line in head.splitlines():
        if line.startswith("Title:"):
            title = line[len("Title:") :].strip() or None
            break
    return title, body
