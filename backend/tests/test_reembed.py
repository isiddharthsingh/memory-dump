"""`python -m app.workers.reembed` gives saved items and wiki pages fresh
embeddings without calling Claude, and leaves alone what changed meanwhile.

Every test passes its throwaway user: a run over everyone would re-embed the
real library with these fake vectors."""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, update

from app.ai import embed
from app.ai.embed import chunk_text
from app.config import get_settings
from app.db.models import EMBED_DIM, Item, ItemChunk, TopicPage, TopicPageChunk
from app.main import app
from app.workers.reembed import Counts, reembed
from tests.conftest import signup

OLD = [0.5] * EMBED_DIM
NEW = [0.1] * EMBED_DIM


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def user_id(client: TestClient) -> Iterator[uuid.UUID]:
    headers = signup(client)
    yield uuid.UUID(client.get("/v1/auth/me", headers=headers).json()["id"])
    client.delete("/v1/auth/me", headers=headers)


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def embedded(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[Callable[[], None]]]:
    """What reaches Voyage, in place of Voyage, and hooks run on each call."""
    texts: list[str] = []
    hooks: list[Callable[[], None]] = []

    async def embed_texts(batch: list[str], input_type: str = "document") -> list[list[float]]:
        assert input_type == "document"
        for hook in hooks:
            hook()
        texts.extend(batch)
        return [NEW for _ in batch]

    monkeypatch.setattr(embed, "embed_texts", embed_texts)
    return texts, hooks


RAW = "\n\n".join(f"Part {i}. " + "Retrieval needs context. " * 60 for i in range(2))
BODY = "\n\n".join(f"## Part {i}\n" + "Pages cite their items. " * 60 for i in range(2))


def _library(db: Any, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """A ready item, a failed one and a page, each with old chunks, and a
    stub page with none."""
    ids = {name: uuid.uuid4() for name in ("ready", "failed", "page", "stub")}
    with db.begin() as conn:
        for name, status in (("ready", "ready"), ("failed", "failed")):
            conn.execute(
                insert(Item.__table__).values(
                    id=ids[name],
                    user_id=user_id,
                    url=f"https://example.com/{name}",
                    source="web",
                    status=status,
                    title="Context in RAG",
                    summary="Why chunks need their document.",
                    tags=["rag", "embeddings"],
                    people=["Ada"],
                    raw_text=RAW,
                    ingest_generation=2,
                )
            )
            conn.execute(
                insert(ItemChunk.__table__).values(
                    item_id=ids[name], user_id=user_id, ord=0, text="old", embedding=OLD
                )
            )
        for name, body in (("page", BODY), ("stub", "_pending_")):
            conn.execute(
                insert(TopicPage.__table__).values(
                    id=ids[name],
                    user_id=user_id,
                    slug=name,
                    title=f"{name.title()} Title",
                    body_md=body,
                    version=3,
                )
            )
        conn.execute(
            insert(TopicPageChunk.__table__).values(
                topic_page_id=ids["page"], user_id=user_id, ord=0, text="old", embedding=OLD
            )
        )
    return ids


def _item_chunks(db: Any, item_id: uuid.UUID) -> list[tuple[str, float]]:
    with db.connect() as conn:
        rows = conn.execute(
            select(ItemChunk.text, ItemChunk.embedding)
            .where(ItemChunk.item_id == item_id)
            .order_by(ItemChunk.ord)
        ).all()
    return [(text, float(vec[0])) for text, vec in rows]


def _page_chunks(db: Any, page_id: uuid.UUID) -> list[tuple[str, float]]:
    with db.connect() as conn:
        rows = conn.execute(
            select(TopicPageChunk.text, TopicPageChunk.embedding)
            .where(TopicPageChunk.topic_page_id == page_id)
            .order_by(TopicPageChunk.ord)
        ).all()
    return [(text, float(vec[0])) for text, vec in rows]


def test_reembed_replaces_a_users_item_and_page_chunks(
    client: TestClient,
    user_id: uuid.UUID,
    db: Any,
    embedded: tuple[list[str], list[Callable[[], None]]],
) -> None:
    texts, _ = embedded
    ids = _library(db, user_id)
    with db.connect() as conn:
        before = conn.execute(select(Item.updated_at).where(Item.id == ids["ready"])).scalar_one()

    counts = client.portal.call(reembed, user_id)  # type: ignore[union-attr]
    assert counts == Counts(items=1, pages=1)

    # Rebuilt from the row as ingest builds it, stored plain.
    item_chunks = chunk_text(
        "Title: Context in RAG\n\nSummary: Why chunks need their document.\n\n"
        "Tags: rag, embeddings\n\nPeople: Ada\n\n" + RAW
    )
    page_chunks = chunk_text(BODY)
    assert len(item_chunks) >= 2 and len(page_chunks) >= 2
    assert _item_chunks(db, ids["ready"]) == [(c, pytest.approx(0.1)) for c in item_chunks]
    assert _page_chunks(db, ids["page"]) == [(c, pytest.approx(0.1)) for c in page_chunks]
    # Embedded with what they belong to in front, after the first.
    context = "Title: Context in RAG\nSummary: Why chunks need their document."
    assert texts == [
        item_chunks[0],
        *(f"{context}\n\n{c}" for c in item_chunks[1:]),
        page_chunks[0],
        *(f"Page Title\n\n{c}" for c in page_chunks[1:]),
    ]

    # Only ready items; a stub gets no chunks; nothing else on the rows moves.
    assert _item_chunks(db, ids["failed"]) == [("old", pytest.approx(0.5))]
    assert _page_chunks(db, ids["stub"]) == []
    with db.connect() as conn:
        assert (
            conn.execute(select(Item.updated_at).where(Item.id == ids["ready"])).scalar_one()
            == before
        )
        assert conn.scalar(select(TopicPage.version).where(TopicPage.id == ids["page"])) == 3

    # Safe to run again.
    assert client.portal.call(reembed, user_id) == Counts(items=1, pages=1)  # type: ignore[union-attr]
    assert _item_chunks(db, ids["ready"]) == [(c, pytest.approx(0.1)) for c in item_chunks]


def test_reembed_skips_an_item_and_a_page_that_changed_meanwhile(
    client: TestClient,
    user_id: uuid.UUID,
    db: Any,
    embedded: tuple[list[str], list[Callable[[], None]]],
) -> None:
    _, hooks = embedded
    ids = _library(db, user_id)

    def changed() -> None:
        # A retry queues a new generation, and synthesis patches the page,
        # while Voyage embeds the old copies.
        with db.begin() as conn:
            conn.execute(
                update(Item.__table__)
                .where(Item.id == ids["ready"])
                .values(ingest_generation=Item.ingest_generation + 1)
            )
            conn.execute(
                update(TopicPage.__table__)
                .where(TopicPage.id == ids["page"])
                .values(version=TopicPage.version + 1)
            )

    hooks.append(changed)
    counts = client.portal.call(reembed, user_id)  # type: ignore[union-attr]
    assert counts == Counts(items_skipped=1, pages_skipped=1)
    assert _item_chunks(db, ids["ready"]) == [("old", pytest.approx(0.5))]
    assert _page_chunks(db, ids["page"]) == [("old", pytest.approx(0.5))]
