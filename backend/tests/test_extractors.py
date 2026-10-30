import httpx
import pytest

from app.extractors.base import detect_source, get_extractor
from app.extractors.reddit import _parse, _to_json_url
from app.extractors.webpage import NotPublic, WebpageExtractor, public_address, public_client
from app.extractors.youtube import extract_video_id


def test_detect_source_dispatch() -> None:
    assert detect_source("https://youtu.be/abc12345678") == "youtube"
    assert detect_source("https://x.com/u/status/1") == "twitter"
    assert detect_source("https://www.instagram.com/p/abc/") == "instagram"
    assert detect_source("https://www.reddit.com/r/python/comments/abc/title/") == "reddit"
    assert detect_source("https://old.reddit.com/r/python/") == "reddit"
    assert detect_source("https://redd.it/abc123") == "reddit"
    assert detect_source("https://example.com/post") == "web"


def test_get_extractor_returns_correct_source() -> None:
    assert get_extractor("youtube").source == "youtube"
    assert get_extractor("twitter").source == "twitter"
    assert get_extractor("instagram").source == "instagram"
    assert get_extractor("reddit").source == "reddit"
    assert get_extractor("web").source == "web"
    # Unknown sources fall back to web.
    assert get_extractor("not-a-source").source == "web"


def test_reddit_to_json_url() -> None:
    assert _to_json_url("https://www.reddit.com/r/python/comments/abc/title/") == (
        "https://www.reddit.com/r/python/comments/abc/title.json?raw_json=1"
    )
    # old.reddit / m.reddit are rewritten to www.
    assert _to_json_url("https://old.reddit.com/r/python/comments/abc/title").startswith(
        "https://www.reddit.com/"
    )
    # redd.it short links become canonical comment URLs.
    assert _to_json_url("https://redd.it/abc123") == (
        "https://www.reddit.com/comments/abc123.json?raw_json=1"
    )
    # Pre-existing query strings are preserved.
    assert "context=3" in _to_json_url(
        "https://www.reddit.com/r/python/comments/abc/title/cmt/?context=3"
    )


def test_reddit_parse_post_with_comments() -> None:
    payload = [
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Why Python is great",
                            "author": "alice",
                            "subreddit": "python",
                            "selftext": "Because it's readable.",
                            "is_self": True,
                            "url": "https://reddit.com/r/python/comments/abc",
                            "thumbnail": "self",
                        },
                    }
                ]
            },
        },
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {"kind": "t1", "data": {"author": "bob", "body": "Agreed."}},
                    {"kind": "t1", "data": {"author": "carol", "body": "[deleted]"}},
                    {"kind": "more", "data": {}},
                ]
            },
        },
    ]
    content = _parse(payload, share_text=None)
    assert content.title == "Why Python is great"
    assert content.author == "alice"
    assert content.site_name == "r/python"
    assert content.thumbnail_url is None
    assert content.raw_text is not None
    assert "Because it's readable." in content.raw_text
    assert "u/bob: Agreed." in content.raw_text
    # Deleted comments are skipped.
    assert "carol" not in content.raw_text


def test_reddit_parse_link_post() -> None:
    payload = [
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "Cool article",
                            "author": "dave",
                            "subreddit": "programming",
                            "selftext": "",
                            "is_self": False,
                            "url_overridden_by_dest": "https://example.com/post",
                            "thumbnail": "https://b.thumbs.redditmedia.com/x.jpg",
                        },
                    }
                ]
            },
        },
        {"kind": "Listing", "data": {"children": []}},
    ]
    content = _parse(payload, share_text="found this")
    assert content.raw_text is not None
    assert "Link: https://example.com/post" in content.raw_text
    assert "User note:\nfound this" in content.raw_text
    assert content.thumbnail_url == "https://b.thumbs.redditmedia.com/x.jpg"


def test_reddit_parse_subreddit_listing() -> None:
    payload = {
        "kind": "Listing",
        "data": {
            "children": [
                {"kind": "t3", "data": {"title": "Post one"}},
                {"kind": "t3", "data": {"title": "Post two"}},
            ]
        },
    }
    content = _parse(payload, share_text=None)
    assert content.raw_text is not None
    assert "- Post one" in content.raw_text
    assert "- Post two" in content.raw_text


def test_extract_video_id_youtube() -> None:
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://example.com") is None
    assert extract_video_id("https://www.youtube.com/watch") is None


def test_chunk_text_packs_paragraphs() -> None:
    from app.ai.embed import chunk_text

    text = "\n\n".join(["short para"] * 5)
    chunks = chunk_text(text, max_chars=1000)
    assert len(chunks) == 1  # fits in one chunk

    big = "x" * 5000
    chunks = chunk_text(big, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


async def test_public_address_refuses_this_machine_and_its_network() -> None:
    # A post's author picks the link, so nothing on this machine or its network.
    for host in ("localhost", "127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.9", "::1", ""):
        with pytest.raises(NotPublic):
            await public_address(host)
    assert await public_address("8.8.8.8") == "8.8.8.8"


async def test_public_only_transport_checks_each_connection() -> None:
    # Checked as the connection opens, which also covers every redirect hop.
    async with public_client() as client:
        for url in ("http://127.0.0.1:9/", "http://169.254.169.254/latest/meta-data/"):
            with pytest.raises(NotPublic):
                await client.get(url)


async def test_a_shared_link_to_a_private_address_is_not_fetched() -> None:
    for url in ("http://localhost:8000/healthz", "http://10.0.0.5/admin"):
        with pytest.raises(NotPublic):
            await WebpageExtractor().extract(url)


async def test_a_name_that_does_not_resolve_is_a_connection_error() -> None:
    # Not "private": a DNS failure fails like it always did.
    async with public_client() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("http://no-such-host.invalid/")
