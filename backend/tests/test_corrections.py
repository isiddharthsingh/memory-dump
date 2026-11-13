"""Corrections: what the user says a wiki page gets wrong, the rewrite that
follows, and every later patch agreeing with them. Claude and Voyage are
stubbed."""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, update

from app.ai import synthesize as synthesize_ai
from app.ai.synthesize import patch_page
from app.api import wiki as wiki_api
from app.api.wiki import MAX_CORRECTIONS
from app.config import get_settings
from app.db.models import EMBED_DIM, Item, TopicPage, TopicPageChunk
from app.main import app
from app.workers import synthesize as synthesize_worker
from app.workers.correct import correct_page
from app.workers.synthesize import synthesize_topics
from tests.conftest import Jobs, signup

X = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def account(client: TestClient) -> Iterator[tuple[dict[str, str], uuid.UUID]]:
    headers = signup(client)
    yield headers, uuid.UUID(client.get("/auth/me", headers=headers).json()["id"])
    client.delete("/auth/me", headers=headers)


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def embed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def embed_page_chunks(body: str, title: str) -> list[tuple[str, list[float]]]:
        return [(body, [0.1] * EMBED_DIM)]

    monkeypatch.setattr(synthesize_ai, "embed_page_chunks", embed_page_chunks)
    monkeypatch.setattr(synthesize_worker, "embed_page_chunks", embed_page_chunks)


def _page(db: Any, user_id: uuid.UUID, slug: str = "ramen", **values: Any) -> uuid.UUID:
    page_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(TopicPage.__table__).values(
                id=page_id,
                user_id=user_id,
                slug=slug,
                title="Ramen",
                **{"body_md": f"Ichiran is open [item:{X}].", "source_item_ids": [X], **values},
            )
        )
    return page_id


def _saved(db: Any, page_id: uuid.UUID) -> Any:
    with db.connect() as conn:
        return conn.execute(
            select(TopicPage.body_md, TopicPage.version, TopicPage.corrections).where(
                TopicPage.id == page_id
            )
        ).one()


def test_a_correction_is_kept_on_the_page_and_queues_its_rewrite(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any, jobs: Jobs
) -> None:
    auth, user_id = account
    page_id = _page(db, user_id)

    resp = client.post(
        "/v1/wiki/ramen/corrections", json={"text": " It closed in 2025. "}, headers=auth
    )
    assert resp.status_code == 200, resp.text
    (correction,) = resp.json()["corrections"]
    assert correction["text"] == "It closed in 2025."
    assert jobs == [
        ("correct_page", str(user_id), str(page_id), {"_job_id": f"correct:{correction['id']}"})
    ]
    # The page says so wherever it is read.
    assert client.get("/v1/wiki/ramen", headers=auth).json()["corrections"] == [correction]

    assert (
        client.post("/v1/wiki/ramen/corrections", json={"text": "  "}, headers=auth).status_code
        == 422
    )
    assert (
        client.post("/v1/wiki/nothing/corrections", json={"text": "No."}, headers=auth).status_code
        == 404
    )
    assert client.post("/v1/wiki/ramen/corrections", json={"text": "No."}).status_code == 401

    removed = client.delete(f"/v1/wiki/ramen/corrections/{correction['id']}", headers=auth)
    assert removed.status_code == 200 and removed.json()["corrections"] is None
    again = client.delete(f"/v1/wiki/ramen/corrections/{correction['id']}", headers=auth)
    assert again.status_code == 404


def test_a_correction_is_kept_when_its_rewrite_cant_be_queued(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    page_id = _page(db, user_id)

    async def enqueue(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("redis is away")

    monkeypatch.setattr(wiki_api, "enqueue", enqueue)
    resp = client.post("/v1/wiki/ramen/corrections", json={"text": "It closed."}, headers=auth)
    # Not an error: the user would send it again, and the page keeps it anyway.
    assert resp.status_code == 200
    assert [c["text"] for c in _saved(db, page_id).corrections] == ["It closed."]


def test_a_page_keeps_a_limited_number_of_corrections_and_only_its_owners(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any, jobs: Jobs
) -> None:
    auth, user_id = account
    full = [
        {"id": str(uuid.uuid4()), "text": f"Fix {n}", "at": "2026-11-01T00:00:00+00:00"}
        for n in range(MAX_CORRECTIONS)
    ]
    _page(db, user_id, corrections=full)
    resp = client.post("/v1/wiki/ramen/corrections", json={"text": "One more."}, headers=auth)
    assert resp.status_code == 409 and jobs == []

    stranger = signup(client)
    try:
        resp = client.post(
            "/v1/wiki/ramen/corrections", json={"text": "Mine now."}, headers=stranger
        )
        assert resp.status_code == 404
        resp = client.delete(f"/v1/wiki/ramen/corrections/{full[0]['id']}", headers=stranger)
        assert resp.status_code == 404
    finally:
        client.delete("/auth/me", headers=stranger)


def test_the_rewrite_agrees_with_the_corrections_and_starts_again_when_the_page_moved(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    corrections = [
        {"id": str(uuid.uuid4()), "text": text, "at": f"2026-11-0{n}T00:00:00+00:00"}
        for n, text in enumerate(("It closed in 2025.", "It was in Shibuya."), 1)
    ]
    page_id = _page(db, user_id, corrections=corrections)
    drafted_from: list[dict[str, Any]] = []

    async def correct(**kwargs: Any) -> str:
        drafted_from.append(kwargs)
        if len(drafted_from) == 1:
            # Synthesis patches the page while Claude drafts this rewrite.
            with db.begin() as conn:
                conn.execute(
                    update(TopicPage.__table__)
                    .where(TopicPage.id == page_id)
                    .values(body_md="Patched meanwhile.", version=2)
                )
        return kwargs["body"] + " Corrected, see [[tokyo]]."

    monkeypatch.setattr(synthesize_ai, "correct_page", correct)
    outcome = client.portal.call(correct_page, {}, str(user_id), str(page_id))  # type: ignore[union-attr]
    assert outcome == "rewritten"
    assert [d["body"] for d in drafted_from] == [
        f"Ichiran is open [item:{X}].",
        "Patched meanwhile.",
    ]
    assert drafted_from[0]["corrections"] == ["It closed in 2025.", "It was in Shibuya."]

    page = _saved(db, page_id)
    assert (page.body_md, page.version) == ("Patched meanwhile. Corrected, see [[tokyo]].", 3)
    with db.connect() as conn:
        chunks = conn.execute(
            select(TopicPageChunk.text).where(TopicPageChunk.topic_page_id == page_id)
        ).scalars()
        assert list(chunks) == [page.body_md]
        backlinks = conn.execute(
            select(TopicPage.backlinks).where(TopicPage.id == page_id)
        ).scalar_one()
    assert backlinks == ["tokyo"]


def test_a_page_without_a_rewrite_stays_as_it_is(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    correction = {"id": str(uuid.uuid4()), "text": "No.", "at": "2026-11-01T00:00:00+00:00"}
    page_id = _page(db, user_id, corrections=[correction])
    plain = _page(db, user_id, slug="plain")

    async def refused(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(synthesize_ai, "correct_page", refused)
    run = client.portal.call  # type: ignore[union-attr]
    assert run(correct_page, {}, str(user_id), str(page_id)) == "kept"
    # Nothing to agree with, someone else's page, a page that's gone.
    assert run(correct_page, {}, str(user_id), str(plain)) == "nothing"
    assert run(correct_page, {}, str(uuid.uuid4()), str(page_id)) == "gone"
    assert run(correct_page, {}, str(user_id), str(uuid.uuid4())) == "gone"
    page = _saved(db, page_id)
    assert (page.body_md, page.version, page.corrections) == (
        f"Ichiran is open [item:{X}].",
        1,
        [correction],
    )


async def test_every_patch_of_a_page_reads_its_corrections(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def claude(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Body.")]
        )

    monkeypatch.setattr(synthesize_ai, "claude", claude)
    for corrections in (["It closed\nin 2025.", "It was in Shibuya."], None):
        await patch_page(
            slug="ramen",
            existing_body="Ichiran is open.",
            existing_title=None,
            item_id=X,
            item_title="Item",
            item_summary=None,
            item_text="Ichiran is open late.",
            related_slugs=[],
            corrections=corrections,
        )
    item_blocks = [c["messages"][0]["content"][0]["text"] for c in calls]
    with_corrections, without = (c["messages"][0]["content"][1]["text"] for c in calls)
    assert (
        "=== The user's corrections to this page, oldest first ===\n"
        "- It closed in 2025.\n- It was in Shibuya.\n\n" in with_corrections
    )
    assert "corrections" not in without
    # They ride with the page, so the item's cached block is the same either way.
    assert item_blocks[0] == item_blocks[1]
    assert "corrections" in calls[0]["system"]


def test_synthesis_patches_a_page_with_its_corrections(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    correction = {"id": str(uuid.uuid4()), "text": "It closed.", "at": "2026-11-01T00:00:00+00:00"}
    page_id = _page(db, user_id, corrections=[correction])
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                source="web",
                status="ready",
                title="Ichiran again",
            )
        )
    patched: list[dict[str, Any]] = []

    async def propose_slugs(**kwargs: Any) -> Any:
        return SimpleNamespace(slugs=["ramen"])

    async def patch(**kwargs: Any) -> Any:
        patched.append(kwargs)
        return synthesize_ai.PatchedPage("ramen", "Ramen", "Patched.", [])

    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", patch)
    outcome = client.portal.call(synthesize_topics, {}, str(item_id))  # type: ignore[union-attr]
    assert outcome == "ok:ramen"
    assert [p["corrections"] for p in patched] == [["It closed."]]
    # The patch leaves the corrections on the page.
    assert _saved(db, page_id).corrections == [correction]
