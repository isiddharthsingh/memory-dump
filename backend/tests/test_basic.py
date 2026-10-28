"""Tests that don't need a running database or external services."""

from fastapi.testclient import TestClient

from app.ai.synthesize import title_from_slug
from app.api.ingest import detect_source
from app.api.search import _snippet
from app.main import app


def test_detect_source_youtube() -> None:
    assert detect_source("https://www.youtube.com/watch?v=abc") == "youtube"
    assert detect_source("https://youtu.be/abc") == "youtube"


def test_detect_source_twitter() -> None:
    assert detect_source("https://x.com/foo/status/1") == "twitter"
    assert detect_source("https://twitter.com/foo/status/1") == "twitter"


def test_detect_source_instagram() -> None:
    assert detect_source("https://www.instagram.com/p/abc/") == "instagram"


def test_detect_source_web() -> None:
    assert detect_source("https://example.com/posts/1") == "web"


def test_healthz() -> None:
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_ingest_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/ingest", json={"url": "https://example.com"})
        assert resp.status_code == 401


def test_snippet_drops_embedding_labels() -> None:
    chunk = (
        "Title: A Recipe for Training Neural Networks\n\n"
        "Summary: Karpathy outlines a methodology.\n\n"
        "Tags: neural-networks, training\n\n"
        "People: Andrej Karpathy\n\n"
        "Body:\nSome few weeks ago I posted a tweet."
    )
    snippet = _snippet(chunk)
    assert snippet == "Karpathy outlines a methodology. Some few weeks ago I posted a tweet."


def test_snippet_strips_wiki_markdown() -> None:
    chunk = (
        "# Large Language Models\n\n"
        "## Overview\nAn LLM is **two files** [item:3f1c2d4e-0000-0000-0000-000000000000], "
        "see [[llm-training]]."
    )
    assert _snippet(chunk) == "An LLM is two files, see llm training."


def test_snippet_keeps_plain_text() -> None:
    assert _snippet("Plain wiki paragraph.\nSecond line.") == "Plain wiki paragraph. Second line."


def test_title_from_slug_keeps_acronyms() -> None:
    assert title_from_slug("llm-scaling") == "LLM Scaling"
    assert title_from_slug("ai-reasoning") == "AI Reasoning"
    assert title_from_slug("large-language-models") == "Large Language Models"
