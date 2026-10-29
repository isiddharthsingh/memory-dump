import uuid

import pytest
from fastapi.testclient import TestClient

from app.ai import retrieval
from app.ai.retrieval import Candidate, _rrf_merge, hybrid_search
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


# Rerank scores for the three chunks the patched searches below return. Dense
# search always returns nearest neighbours, even for nonsense, so a chunk found
# only that way needs a good rerank score. Keyword matches stay.
SCORES = {"on topic": 0.8, "nearest neighbour of nonsense": 0.4, "contains the query word": 0.3}


@pytest.fixture
def three_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    near, far, keyword = (_chunk(text) for text in SCORES)

    async def embed(texts: list[str], input_type: str) -> list[list[float]]:
        return [[0.0]]

    async def dense(*_: object) -> list[Candidate]:
        return [near, far, keyword]

    async def lexical(*_: object) -> list[Candidate]:
        return [keyword]

    monkeypatch.setattr(retrieval, "embed_texts", embed)
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
    hits = await hybrid_search(None, "q", uuid.uuid4(), final_limit=25, min_score=0.5)  # type: ignore[arg-type]
    assert [h.text for h in hits] == ["on topic", "contains the query word"]


async def test_min_score_keeps_only_keyword_hits_when_rerank_fails(
    three_chunks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rerank(*_: object, **__: object) -> list[tuple[int, float]]:
        raise RuntimeError("rate limited")

    monkeypatch.setattr(retrieval, "rerank", rerank)
    hits = await hybrid_search(None, "q", uuid.uuid4(), final_limit=25, min_score=0.5)  # type: ignore[arg-type]
    assert [h.text for h in hits] == ["contains the query word"]


def test_ask_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/ask", json={"question": "anything?"})
        assert resp.status_code == 401


def test_search_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/search", json={"q": "anything?"})
        assert resp.status_code == 401
