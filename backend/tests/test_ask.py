from fastapi.testclient import TestClient

from app.ai.retrieval import _rrf_merge
from app.main import app


def test_rrf_merge_basic() -> None:
    a = [("item", "1"), ("item", "2"), ("item", "3")]
    b = [("item", "3"), ("item", "1"), ("item", "2")]
    scores = _rrf_merge([a, b])
    # item 1 is rank 0 in a and rank 1 in b → 1/61 + 1/62
    # item 3 is rank 2 in a and rank 0 in b → 1/63 + 1/61
    # item 1 should outscore item 2 (which is rank 1, rank 2)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    assert ranked[0][0] == ("item", "1") or ranked[0][0] == ("item", "3")
    assert {key for key, _ in ranked} == {("item", "1"), ("item", "2"), ("item", "3")}


def test_ask_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/ask", json={"question": "anything?"})
        assert resp.status_code == 401


def test_search_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/search", json={"q": "anything?"})
        assert resp.status_code == 401
