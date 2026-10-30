import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from voyageai.error import RateLimitError

from app.ai import ask as ask_module
from app.ai import retrieval
from app.ai.retrieval import Candidate, _rrf_merge, hybrid_search
from app.errors import ProviderUnavailable
from app.main import app
from tests.conftest import signup


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


def _chunk(text: str) -> Candidate:
    return Candidate(
        kind="item",
        chunk_id=uuid.uuid4(),
        parent_id=uuid.uuid4(),
        ord=0,
        text=text,
        title=None,
        url=None,
        slug=None,
    )


class _Session:
    """Stands in for the AsyncSession the patched searches never touch."""

    async def commit(self) -> None:
        pass


# Rerank scores for the three chunks the patched searches below return. Dense
# search always returns nearest neighbours, even for nonsense, so a chunk found
# only that way needs a good rerank score. Keyword matches stay.
SCORES = {"on topic": 0.8, "nearest neighbour of nonsense": 0.4, "contains the query word": 0.3}


@pytest.fixture
def three_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    near, far, keyword = (_chunk(text) for text in SCORES)

    async def embed(query: str) -> list[float]:
        return [0.0]

    async def dense(*_: object) -> list[Candidate]:
        return [near, far, keyword]

    async def lexical(*_: object) -> list[Candidate]:
        return [keyword]

    monkeypatch.setattr(retrieval, "embed_query", embed)
    monkeypatch.setattr(retrieval, "_dense_search", dense)
    monkeypatch.setattr(retrieval, "_lexical_search", lexical)


async def test_min_score_drops_weak_dense_only_hits(
    three_chunks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rerank(
        query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        ranked = sorted(enumerate(documents), key=lambda d: SCORES[d[1]], reverse=True)
        return [(i, SCORES[doc]) for i, doc in ranked][:top_k]

    monkeypatch.setattr(retrieval, "rerank", rerank)
    # Fewer candidates than the limit: still reranked, so the cutoff applies.
    hits = await hybrid_search(_Session(), "q", uuid.uuid4(), final_limit=25, min_score=0.5)  # type: ignore[arg-type]
    assert [h.text for h in hits] == ["on topic", "contains the query word"]


async def test_min_score_keeps_only_keyword_hits_when_rerank_fails(
    three_chunks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rerank(*_: object, **__: object) -> list[tuple[int, float]]:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(retrieval, "rerank", rerank)
    hits = await hybrid_search(_Session(), "q", uuid.uuid4(), final_limit=25, min_score=0.5)  # type: ignore[arg-type]
    assert [h.text for h in hits] == ["contains the query word"]


def test_ask_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/ask", json={"question": "anything?"})
        assert resp.status_code == 401


def test_search_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/search", json={"q": "anything?"})
        assert resp.status_code == 401


async def test_search_falls_back_to_keywords_when_the_query_cant_be_embedded(
    three_chunks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def embed(query: str) -> list[float]:
        raise ProviderUnavailable("voyage", 30)

    async def dense(*_: object) -> list[Candidate]:
        raise AssertionError("no embedding to search with")

    async def rerank(*_: object, **__: object) -> list[tuple[int, float]]:
        raise ProviderUnavailable("voyage", 30)

    monkeypatch.setattr(retrieval, "embed_query", embed)
    monkeypatch.setattr(retrieval, "_dense_search", dense)
    monkeypatch.setattr(retrieval, "rerank", rerank)
    hits = await hybrid_search(_Session(), "q", uuid.uuid4(), final_limit=25, min_score=0.5)  # type: ignore[arg-type]
    assert [h.text for h in hits] == ["contains the query word"]


def test_an_unavailable_provider_is_a_503_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ask(*_: object) -> None:
        raise ProviderUnavailable("anthropic", 42)

    async def ask_stream(*_: object) -> AsyncIterator[object]:
        raise RateLimitError("slow down")
        yield

    monkeypatch.setattr(ask_module, "ask", ask)
    monkeypatch.setattr(ask_module, "ask_stream", ask_stream)
    with TestClient(app) as client:
        auth = signup(client)
        try:
            resp = client.post("/v1/ask", json={"question": "anything?"}, headers=auth)
            assert resp.status_code == 503
            assert resp.headers["retry-after"] == "42"
            assert "Try again" in resp.json()["detail"]
            stream = client.post("/v1/ask/stream", json={"question": "anything?"}, headers=auth)
            assert stream.text.startswith("event: error\n")
            # No exception text from the provider reaches the app.
            assert "slow down" not in stream.text
        finally:
            client.delete("/auth/me", headers=auth)
