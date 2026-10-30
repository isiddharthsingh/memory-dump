"""Tests that don't need a running database or external services."""

import pytest
from fastapi.testclient import TestClient

from app.ai.synthesize import title_from_slug
from app.api.ingest import detect_source
from app.api.search import _snippet
from app.config import get_settings
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


def test_routes_answer_with_and_without_v1() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/items").status_code == 401
        assert client.get("/items").status_code == 401
        assert client.get("/v1/nothing-here").status_code == 404


def test_old_builds_are_asked_to_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "min_app_build", 10)
    with TestClient(app) as client:
        too_old = client.get("/v1/items", headers={"X-App-Build": "9"})
        assert too_old.status_code == 426
        assert "Update" in too_old.json()["detail"]
        assert client.get("/v1/items", headers={"X-App-Build": "10"}).status_code == 401
        # Builds from before /v1 sent no build number and call unversioned routes.
        assert client.get("/items").status_code == 426
        assert client.get("/v1/items").status_code == 401
        assert client.get("/healthz").status_code == 200


def test_readyz_checks_postgres_and_redis() -> None:
    with TestClient(app) as client:
        assert client.get("/livez").json() == {"status": "ok"}
        resp = client.get("/readyz")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok"}


def test_metrics_need_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 404
        monkeypatch.setattr(get_settings(), "metrics_token", "t0ken")
        assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 404
        resp = client.get("/metrics", headers={"Authorization": "Bearer t0ken"})
        assert resp.status_code == 200
        assert "http_requests_total" in resp.text


def test_request_ids_are_kept_or_made() -> None:
    with TestClient(app) as client:
        assert (
            client.get("/livez", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
            == "abc-123"
        )
        made = client.get("/livez").headers["x-request-id"]
        assert len(made) == 32
        bad = client.get("/livez", headers={"X-Request-ID": "has spaces"})
        assert bad.headers["x-request-id"] != "has spaces"
