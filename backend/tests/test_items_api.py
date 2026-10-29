import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.api import ingest as ingest_api
from app.config import get_settings
from app.main import app
from tests.conftest import signup

# A 1×1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client: TestClient) -> Iterator[dict[str, str]]:
    headers = signup(client)
    yield headers
    client.delete("/auth/me", headers=headers)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, ...]]]:
    """Records ingest jobs instead of queueing them, so a running worker can't
    change an item mid-test. Deletes the uploads the worker would have."""
    enqueued: list[tuple[str, ...]] = []

    class Pool:
        async def enqueue_job(self, *args: str) -> None:
            enqueued.append(args)

    async def pool() -> Pool:
        return Pool()

    monkeypatch.setattr(ingest_api, "get_arq_pool", pool)
    yield enqueued
    for job in enqueued:
        if job[0] == "ingest_file":
            Path(job[2]).unlink(missing_ok=True)


def _set_failed(item_id: str) -> None:
    engine = create_engine(get_settings().sync_database_url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE items SET status = 'failed', error = 'boom' WHERE id = :id"),
            {"id": item_id},
        )
    engine.dispose()


def test_list_tags_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/items/tags").status_code == 401


def test_retry_requires_auth() -> None:
    with TestClient(app) as client:
        assert (
            client.post("/items/00000000-0000-0000-0000-000000000000/retry").status_code == 401
        )


def test_sharing_a_saved_link_again_returns_it(
    client: TestClient, auth: dict[str, str], jobs: list[tuple[str, ...]]
) -> None:
    url = f"https://example.com/again/{uuid.uuid4().hex[:8]}"
    first = client.post("/ingest", json={"url": url}, headers=auth)
    second = client.post("/ingest", json={"url": url}, headers=auth)
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["item"]["id"] == first.json()["item"]["id"]
    assert len([i for i in client.get("/items", headers=auth).json() if i["url"] == url]) == 1
    assert len(jobs) == 1


def test_sharing_a_failed_link_again_retries_it(
    client: TestClient, auth: dict[str, str], jobs: list[tuple[str, ...]]
) -> None:
    url = f"https://example.com/failed/{uuid.uuid4().hex[:8]}"
    item_id = client.post("/ingest", json={"url": url}, headers=auth).json()["item"]["id"]
    _set_failed(item_id)
    again = client.post("/ingest", json={"url": url}, headers=auth)
    assert again.status_code == 202, again.text
    item = again.json()["item"]
    assert item["id"] == item_id
    assert item["status"] == "pending"
    assert item["error"] is None
    assert jobs == [("ingest_item", item_id)] * 2


def test_files_with_the_same_name_are_separate_items(
    client: TestClient, auth: dict[str, str], jobs: list[tuple[str, ...]]
) -> None:
    ids = []
    for _ in range(2):
        resp = client.post(
            "/ingest/file", files={"file": ("image.png", PNG, "image/png")}, headers=auth
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["item"]["title"] == "image.png"
        ids.append(resp.json()["item"]["id"])
    assert ids[0] != ids[1]
