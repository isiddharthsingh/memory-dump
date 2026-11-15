"""A shared Google Maps link saves the place it points to. Google answers a
browser with a script page that says nothing, and a link-preview client or a
crawler with a redirect to an address that names the place, and a page about
it."""

from collections.abc import Callable

import httpx
import pytest

from app.extractors.maps import is_maps_link, place_text
from app.extractors.webpage import GOOGLEBOT, LINK_PREVIEW, SAFARI, WebpageExtractor
from tests.conftest import Handler

SHORT = "https://maps.app.goo.gl/xupFizHSz829HRnZ9"
PLACE = (
    "https://www.google.com/maps?q=Punjabi+Rasoi,+1483+NJ-27,+Somerset,+NJ+08873"
    "&ftid=0x89c3c6bbf66a9477:0x6a610b049bb4f384&entry=gps"
)
TEXT = "Punjabi Rasoi, 1483 NJ-27, Somerset, NJ 08873"
PHOTO = "https://lh3.googleusercontent.com/grass-cs/photo"
STATIC_MAP = "https://maps.google.com/maps/api/staticmap?center=40.65%2C-74.01&zoom=11"
ARTICLE = (
    '<html><head><meta property="og:title" content="A post"></head><body><article><p>'
    + "Words worth keeping. " * 20
    + "</p></article></body></html>"
)
# What Google sends when it has nothing to say about the place: its own name,
# its own description and a map of somewhere else. To a browser, in the
# link's language, and to a crawler.
GENERIC = [
    ("Google Maps", "Find local businesses, view maps and get driving directions in Google Maps."),
    ("Google マップ", "Google マップで地図を検索。乗換案内、路線図、ドライブルートも。"),
    ("- Google Maps", "View details about  on Google Maps"),
]


def _page(title: str, image: str, description: str = "") -> str:
    return (
        f'<html><head><title>{title}</title><meta property="og:title" content="{title}">'
        f'<meta property="og:image" content="{image}">'
        f'<meta property="og:description" content="{description}"></head>'
        # Every Maps page's only text. It is not about the place.
        "<body><p>When you have eliminated the JavaScript, whatever remains must be an empty"
        " page.</p><p>Enable JavaScript to see Google Maps.</p></body></html>"
    )


def _html(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"Content-Type": "text/html"})


ABOUT = _page("Punjabi Rasoi · Somerset, New Jersey", PHOTO, "★★★★★ · Indian restaurant")


class Google:
    """Google Maps as it answers: the short link is a script page for a
    browser and a redirect for anything else. `blocks` are the clients it
    turns away. Keeps who asked for what."""

    def __init__(self, page: str, blocks: tuple[str, ...] = ()) -> None:
        self.page = page
        self.blocks = blocks
        self.asked: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        agent = request.headers["User-Agent"]
        self.asked.append((agent, request.url.host))
        if agent in self.blocks:
            return httpx.Response(429)
        if request.url.host == "maps.app.goo.gl":
            if agent == SAFARI:
                return _html("<html><body><script>go()</script></body></html>")
            return httpx.Response(302, headers={"Location": PLACE})
        return _html(self.page)


def test_a_maps_link_is_known_by_its_host_and_path() -> None:
    for url in (
        SHORT,
        "https://goo.gl/maps/AbCdEf123",
        "https://maps.google.com/?q=Katz%27s+Delicatessen",
        "https://www.google.com/maps/place/Ichiran/@35.66,139.70,17z",
        "https://google.co.in/maps?q=Karim%27s",
    ):
        assert is_maps_link(url), url
    for url in (
        "https://www.google.com/search?q=maps",
        "https://goo.gl/AbCdEf123",
        "https://example.com/maps/place/Ichiran",
        "https://docs.google.com/maps",
        "https://notgoogle.com/maps",
    ):
        assert not is_maps_link(url), url


def test_the_place_is_read_from_the_address_a_maps_link_leads_to() -> None:
    assert place_text(PLACE) == TEXT
    assert (
        place_text(
            "https://www.google.com/maps/place/Katz's+Delicatessen/@40.7223,-73.9874,17z/data=!3m1"
        )
        == "Katz's Delicatessen"
    )
    assert (
        place_text("https://www.google.com/maps/search/?api=1&query=Ichiran%20Shibuya")
        == "Ichiran Shibuya"
    )
    # A point, a view or a route names no place.
    assert place_text("https://www.google.com/maps?q=40.7223,-73.9874") is None
    assert place_text("https://www.google.com/maps/@40.7223,-73.9874,15z") is None
    assert place_text("https://www.google.com/maps/dir/Newark/Somerset") is None
    # The short link itself says nothing, and neither does another site.
    assert place_text(SHORT) is None
    assert place_text("https://example.com/maps?q=Punjabi+Rasoi") is None


async def test_a_short_maps_link_saves_the_place_it_points_to(
    serve: Callable[[Handler], None],
) -> None:
    google = Google(ABOUT)
    serve(google)
    content = await WebpageExtractor().extract(SHORT, share_text="Dinner with\n\nfamily")
    assert content.title == "Punjabi Rasoi · Somerset, New Jersey"
    assert content.raw_text == (
        "Title: Punjabi Rasoi · Somerset, New Jersey\n\n"
        f"Place: {TEXT}\n\n"
        "Summary: ★★★★★ · Indian restaurant\n\n"
        "User note:\nDinner with\nfamily"
    )
    assert content.thumbnail_url == PHOTO
    # Asked once, as a link preview: a browser would get the script page.
    assert google.asked == [(LINK_PREVIEW, "maps.app.goo.gl"), (LINK_PREVIEW, "www.google.com")]


@pytest.mark.parametrize(("title", "description"), GENERIC)
async def test_a_maps_page_about_nothing_still_saves_the_place(
    serve: Callable[[Handler], None], title: str, description: str
) -> None:
    serve(Google(_page(title, STATIC_MAP, description)))
    content = await WebpageExtractor().extract(SHORT)
    # Only what the address says: none of the page's words or its map.
    assert content.title == TEXT
    assert content.raw_text == f"Title: {TEXT}\n\nPlace: {TEXT}"
    assert content.thumbnail_url is None


async def test_a_crawlers_title_loses_the_sites_name(serve: Callable[[Handler], None]) -> None:
    serve(Google(_page("Punjabi Rasoi - Google Maps", PHOTO)))
    content = await WebpageExtractor().extract(SHORT)
    assert content.title == "Punjabi Rasoi"
    assert content.thumbnail_url == PHOTO


async def test_a_maps_link_is_asked_as_a_crawler_when_the_preview_is_turned_away(
    serve: Callable[[Handler], None],
) -> None:
    google = Google(ABOUT, blocks=(LINK_PREVIEW,))
    serve(google)
    content = await WebpageExtractor().extract(SHORT)
    assert content.raw_text is not None and f"Place: {TEXT}" in content.raw_text
    # Never as a browser: its script page would be saved as the item.
    assert [agent for agent, _ in google.asked] == [LINK_PREVIEW, GOOGLEBOT, GOOGLEBOT]


async def test_a_maps_link_google_fails_on_is_tried_again_later(
    serve: Callable[[Handler], None],
) -> None:
    asked: list[str] = []

    def failing(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers["User-Agent"])
        return httpx.Response(503)

    serve(failing)
    # Raised, so the job retries it, instead of saving a browser's empty page.
    with pytest.raises(httpx.HTTPStatusError):
        await WebpageExtractor().extract(SHORT)
    assert asked == [LINK_PREVIEW]


async def test_a_maps_link_that_names_no_place_is_saved_from_its_page(
    serve: Callable[[Handler], None],
) -> None:
    # A place by its id: the address has no name, the page a preview gets does.
    google = Google(
        _page("Punjabi Rasoi · 1483 NJ-27, Somerset, NJ 08873", PHOTO, "★★★★★ · Indian restaurant")
    )
    serve(google)
    content = await WebpageExtractor().extract("https://maps.google.com/?cid=7665420155181396868")
    assert content.title == "Punjabi Rasoi · 1483 NJ-27, Somerset, NJ 08873"
    assert content.raw_text == (
        "Title: Punjabi Rasoi · 1483 NJ-27, Somerset, NJ 08873\n\n"
        "Summary: ★★★★★ · Indian restaurant"
    )
    assert content.thumbnail_url == PHOTO
    assert google.asked == [(LINK_PREVIEW, "maps.google.com")]


async def test_a_link_that_leads_to_maps_saves_the_place(
    serve: Callable[[Handler], None],
) -> None:
    asked: list[str] = []

    def shortener(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers["User-Agent"])
        if request.url.host == "bit.ly":
            return httpx.Response(302, headers={"Location": PLACE})
        return _html(_page(*GENERIC[0][:1], STATIC_MAP, GENERIC[0][1]))

    serve(shortener)
    content = await WebpageExtractor().extract("https://bit.ly/3xAmPlE")
    assert content.raw_text == f"Title: {TEXT}\n\nPlace: {TEXT}"
    # Any other link is asked as a browser first, as before.
    assert asked == [SAFARI, SAFARI]

    asked.clear()
    serve(lambda request: asked.append(request.headers["User-Agent"]) or _html(ARTICLE))
    page = await WebpageExtractor().extract("https://example.com/post")
    assert page.title == "A post"
    assert page.raw_text is not None and page.raw_text.startswith("Title: A post\n\nBody:")
    assert asked == [SAFARI]
