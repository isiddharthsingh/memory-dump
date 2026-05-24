"""Tests that don't need a running database or external services."""

from fastapi.testclient import TestClient

from app.api.ingest import detect_source
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
