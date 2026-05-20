import os

os.environ.setdefault("MEMORY_DUMP_API_TOKEN", "test-token")

from app.extractors.base import detect_source, get_extractor  # noqa: E402
from app.extractors.youtube import extract_video_id  # noqa: E402


def test_detect_source_dispatch() -> None:
    assert detect_source("https://youtu.be/abc12345678") == "youtube"
    assert detect_source("https://x.com/u/status/1") == "twitter"
    assert detect_source("https://www.instagram.com/p/abc/") == "instagram"
    assert detect_source("https://example.com/post") == "web"


def test_get_extractor_returns_correct_source() -> None:
    assert get_extractor("youtube").source == "youtube"
    assert get_extractor("twitter").source == "twitter"
    assert get_extractor("instagram").source == "instagram"
    assert get_extractor("web").source == "web"
    # Unknown sources fall back to web.
    assert get_extractor("not-a-source").source == "web"


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
