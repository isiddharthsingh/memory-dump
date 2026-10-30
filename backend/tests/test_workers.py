"""Jobs against Postgres, with extraction, Claude and Voyage stubbed: ingest
fencing and giving up, the reconciler, synthesis drafting a page again,
nightly compaction, the digest fan-out, and the search queries.

Run via `docker compose exec api pytest tests/test_workers.py`.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from arq import Retry
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, insert, select, update

from app.ai import retrieval
from app.ai.analyze import Analysis
from app.ai.synthesize import PatchedPage, ProposedSlugs
from app.config import get_settings
from app.db.models import EMBED_DIM, Item, ItemChunk, ItemTombstone, TopicPage
from app.db.session import get_sessionmaker
from app.extractors.base import ExtractedContent
from app.main import app
from app.storage import get_storage
from app.workers import compact as compact_worker
from app.workers import ingest as ingest_worker
from app.workers import synthesize as synthesize_worker
from app.workers.compact import compact_user
from app.workers.digest import digest_user, weekly_digest
from app.workers.ingest import ingest_file, ingest_item
from app.workers.reconcile import requeue_stuck
from app.workers.synthesize import synthesize_topics
from tests.conftest import Jobs, signup


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def account(client: TestClient) -> Iterator[tuple[dict[str, str], uuid.UUID]]:
    headers = signup(client)
    yield headers, uuid.UUID(client.get("/v1/auth/me", headers=headers).json()["id"])
    client.delete("/v1/auth/me", headers=headers)


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


def _item(db: Any, user_id: uuid.UUID, **values: Any) -> uuid.UUID:
    item_id = values.pop("id", uuid.uuid4())
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                **{"source": "web", "status": "pending", **values},
            )
        )
    return item_id


def _row(db: Any, item_id: uuid.UUID) -> Any:
    with db.connect() as conn:
        return conn.execute(select(Item.__table__).where(Item.id == item_id)).one()


def _ago(**delta: float) -> Any:
    return func.now() - timedelta(**delta)


class _Extractor:
    def __init__(self, extract: Any) -> None:
        self.extract = extract


def test_an_ingest_overtaken_by_a_newer_request_writes_nothing(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    item_id = _item(db, user_id)

    async def extract(url: str, share_text: str | None = None) -> ExtractedContent:
        # The note is edited while the page is fetched: generation 1 is queued.
        with db.begin() as conn:
            conn.execute(
                update(Item.__table__).where(Item.id == item_id).values(ingest_generation=1)
            )
        return ExtractedContent(title="Old note's page", raw_text="Body")

    async def analyze(*_: Any, **__: Any) -> Analysis:
        return Analysis("Title", "Summary", [], [], [], [], [])

    async def embed_texts(texts: list[str], input_type: str) -> list[list[float]]:
        return [[0.1] * EMBED_DIM for _ in texts]

    monkeypatch.setattr(ingest_worker, "get_extractor", lambda _: _Extractor(extract))
    monkeypatch.setattr(ingest_worker, "analyze", analyze)
    monkeypatch.setattr(ingest_worker, "embed_texts", embed_texts)

    run = client.portal.call  # type: ignore[union-attr]
    assert run(ingest_item, {"job_try": 1}, str(item_id), 0) == "superseded"
    row = _row(db, item_id)
    assert (row.status, row.title, row.ingest_generation) == ("processing", None, 1)
    with db.connect() as conn:
        assert conn.scalar(select(func.count()).where(ItemChunk.item_id == item_id)) == 0
    # Run again, generation 0 isn't even claimed.
    assert run(ingest_item, {"job_try": 1}, str(item_id), 0) == "skipped"


def test_a_site_failure_retries_then_fails_on_the_last_try(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    item_id = _item(db, user_id)

    async def extract(url: str, share_text: str | None = None) -> ExtractedContent:
        raise httpx.ConnectError("site is down")

    monkeypatch.setattr(ingest_worker, "get_extractor", lambda _: _Extractor(extract))
    run = client.portal.call  # type: ignore[union-attr]
    with pytest.raises(Retry):
        run(ingest_item, {"job_try": 1}, str(item_id), 0)
    row = _row(db, item_id)
    assert (row.status, row.error) == ("pending", "retrying: ConnectError")

    # Sites get three tries; on the last the item fails instead of being
    # dropped by arq with "pending" left on it.
    assert run(ingest_item, {"job_try": 3}, str(item_id), 0) == "failed"
    row = _row(db, item_id)
    assert row.status == "failed"
    assert row.error.startswith("ConnectError")


def test_the_reconciler_puts_back_lost_jobs_and_fails_stuck_ones(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any, jobs: Jobs
) -> None:
    _, user_id = account
    lost = _item(db, user_id, ingest_generation=2, updated_at=_ago(minutes=20))
    fresh = _item(db, user_id)
    stuck = _item(db, user_id, status="processing", updated_at=_ago(minutes=30))
    unsynthesized = _item(
        db,
        user_id,
        status="ready",
        wiki_status="pending",
        ingest_generation=1,
        updated_at=_ago(hours=1),
    )
    old_tombstone = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(ItemTombstone.__table__).values(
                item_id=old_tombstone,
                user_id=user_id,
                created_at=_ago(days=60),
                deleted_at=_ago(days=40),
            )
        )

    client.portal.call(requeue_stuck, {})  # type: ignore[union-attr]
    assert ("ingest_item", str(lost), {"generation": 2, "_job_id": f"ingest:{lost}:2"}) in jobs
    assert (
        "synthesize_topics",
        str(unsynthesized),
        {"_job_id": f"synthesize:{unsynthesized}:1"},
    ) in jobs
    assert not [job for job in jobs if job[1] == str(fresh)]
    row = _row(db, stuck)
    assert row.status == "failed"
    assert row.error.startswith("TimeoutError")
    with db.connect() as conn:
        assert (
            conn.scalar(select(ItemTombstone.item_id).where(ItemTombstone.item_id == old_tombstone))
            is None
        )


@pytest.fixture
def embed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def embed_page_chunks(body: str) -> list[tuple[str, list[float]]]:
        return [(body, [0.1] * EMBED_DIM)]

    monkeypatch.setattr(synthesize_worker, "embed_page_chunks", embed_page_chunks)
    monkeypatch.setattr(compact_worker, "embed_page_chunks", embed_page_chunks)


def _page(
    db: Any, user_id: uuid.UUID, slug: str, body: str, sources: list[uuid.UUID] | None
) -> None:
    with db.begin() as conn:
        conn.execute(
            insert(TopicPage.__table__).values(
                user_id=user_id,
                slug=slug,
                title=slug,
                body_md=body,
                source_item_ids=sources,
                version=1,
            )
        )


def test_synthesis_drafts_a_page_again_when_another_job_changed_it(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    item_id = _item(db, user_id, status="ready", title="Rust notes", wiki_status="pending")
    _page(db, user_id, "rust", "Old.", None)
    drafted_from: list[str] = []

    async def propose_slugs(**_: Any) -> ProposedSlugs:
        return ProposedSlugs(slugs=["rust"])

    async def patch_page(**kwargs: Any) -> PatchedPage:
        drafted_from.append(kwargs["existing_body"])
        if len(drafted_from) == 1:
            # Another job writes the page while Claude drafts this one.
            with db.begin() as conn:
                conn.execute(
                    update(TopicPage.__table__)
                    .where(TopicPage.user_id == user_id, TopicPage.slug == "rust")
                    .values(body_md="Changed.", version=2)
                )
        body = f"{kwargs['existing_body']} New [item:{item_id}]."
        return PatchedPage(slug="rust", title="Rust", body_md=body, backlinks=[])

    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", patch_page)

    assert client.portal.call(synthesize_topics, {}, str(item_id)) == "ok:rust"  # type: ignore[union-attr]
    assert drafted_from == ["Old.", "Changed."]
    page = client.get("/v1/wiki/rust", headers=auth).json()
    assert page["body_md"] == f"Changed. New [item:{item_id}]."
    assert page["version"] == 3
    assert page["source_item_ids"] == [str(item_id)]
    assert _row(db, item_id).wiki_status == "done"


def test_compaction_merges_duplicates_and_queues_forgotten_items(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    jobs: Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    x = _item(db, user_id, status="ready")
    y = _item(db, user_id, status="ready")
    gone = uuid.uuid4()
    _page(db, user_id, "rust-async", f"A [item:{x}]. See [[async-rust]].", [x])
    _page(db, user_id, "async-rust", f"B [item:{y}].", [y])
    _page(db, user_id, "cooking", f"C [item:{x}].", [x])
    _page(db, user_id, "cites-a-deleted-item", f"D [item:{gone}].", [gone])

    async def claude(**_: Any) -> SimpleNamespace:
        plan = '{"merges": [{"keep": "rust-async", "absorb": ["async-rust"]}]}'
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=plan)])

    monkeypatch.setattr(compact_worker, "claude", claude)
    result = client.portal.call(compact_user, {}, str(user_id))  # type: ignore[union-attr]
    assert result == "forgets:1,merged:1"
    assert jobs == [("forget_item", str(user_id), str(gone), {"_job_id": f"forget:{gone}"})]
    slugs = {p["slug"] for p in client.get("/v1/wiki", headers=auth).json()}
    assert slugs == {"rust-async", "cooking", "cites-a-deleted-item"}
    kept = client.get("/v1/wiki/rust-async", headers=auth).json()
    assert kept["body_md"] == (
        f"A [item:{x}]. See [[rust-async]].\n\n## Merged from async-rust\n\nB [item:{y}]."
    )
    assert kept["source_item_ids"] == [str(x), str(y)]
    assert kept["version"] == 2


def test_the_weekly_digest_queues_one_job_per_user_per_week(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], jobs: Jobs
) -> None:
    _, user_id = account
    client.portal.call(weekly_digest, {})  # type: ignore[union-attr]
    year, week, _ = datetime.now(UTC).isocalendar()
    assert (
        "digest_user",
        str(user_id),
        {"_job_id": f"digest:{user_id}:{year}-W{week:02d}"},
    ) in jobs


def test_keyword_and_vector_search_find_the_users_chunks(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any
) -> None:
    _, user_id = account
    item_id = _item(db, user_id, status="ready", title="Zanzibar cloves")
    with db.begin() as conn:
        conn.execute(
            insert(ItemChunk.__table__).values(
                id=uuid.uuid4(),
                item_id=item_id,
                user_id=user_id,
                ord=0,
                text="Notes on the spice trade",
                embedding=[0.1] * EMBED_DIM,
            )
        )

    async def search() -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        async with get_sessionmaker()() as session:
            by_chunk = await retrieval._lexical_search(session, "spice", 10, user_id)
            by_item = await retrieval._lexical_search(session, "zanzibar", 10, user_id)
            nothing = await retrieval._lexical_search(session, "volcano", 10, user_id)
            dense = await retrieval._dense_search(session, [0.1] * EMBED_DIM, 10, user_id)
            return by_chunk, by_item, nothing, dense

    by_chunk, by_item, nothing, dense = client.portal.call(search)  # type: ignore[union-attr]
    assert [c.parent_id for c in by_chunk] == [item_id]
    assert [c.parent_id for c in by_item] == [item_id]
    assert nothing == []
    assert [c.parent_id for c in dense] == [item_id]


def test_a_users_digest_counts_the_weeks_saves(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any
) -> None:
    _, user_id = account
    run = client.portal.call  # type: ignore[union-attr]
    assert run(digest_user, {}, str(user_id)) == "nothing"
    _item(db, user_id, status="ready", title="Saved this week")
    # No devices registered, so nothing is sent, but the week's saves are found.
    assert run(digest_user, {}, str(user_id)) == "devices:0"


def test_a_superseded_upload_job_leaves_the_file_to_the_newer_one(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    key = f"{uuid.uuid4()}.pdf"
    item_id = _item(db, user_id, source="pdf", upload_key=key, upload_mime="application/pdf")

    async def chunks() -> AsyncIterator[bytes]:
        yield b"%PDF-1.4"

    class Vision:
        async def analyze_pdf(self, data: bytes, filename: str) -> None:
            # The note is edited meanwhile, queueing generation 1.
            with db.begin() as conn:
                conn.execute(
                    update(Item.__table__).where(Item.id == item_id).values(ingest_generation=1)
                )
            raise ValueError("not a PDF Claude can read")

    monkeypatch.setattr(ingest_worker, "get_vision_provider", Vision)
    run = client.portal.call  # type: ignore[union-attr]
    run(get_storage().save, key, chunks())
    try:
        assert run(ingest_file, {"job_try": 1}, str(item_id), None, None, 0) == "superseded"
        assert run(get_storage().read, key) == b"%PDF-1.4"
        assert _row(db, item_id).status == "processing"
    finally:
        run(get_storage().delete, key)
