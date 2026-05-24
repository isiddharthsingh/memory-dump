from fastapi.testclient import TestClient

from app.ai.synthesize import (
    WIKILINK_RE,
    _normalize_slug,
    _strip_json_fence,
)
from app.main import app


def test_normalize_slug() -> None:
    assert _normalize_slug("Rust Async Runtimes!") == "rust-async-runtimes"
    assert _normalize_slug("  LLM Wiki  ") == "llm-wiki"


def test_strip_json_fence() -> None:
    assert _strip_json_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_json_fence('{"a":1}') == '{"a":1}'


def test_wikilink_regex() -> None:
    matches = WIKILINK_RE.findall("see [[rust-async]] and [[llm-wiki]] for more")
    assert matches == ["rust-async", "llm-wiki"]


def test_wiki_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/wiki").status_code == 401
        assert client.get("/wiki/anything").status_code == 401
