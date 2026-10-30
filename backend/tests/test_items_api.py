import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.main import app
from tests.conftest import Jobs, signup

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
    client: TestClient, auth: dict[str, str], jobs: Jobs
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
    client: TestClient, auth: dict[str, str], jobs: Jobs
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
    assert jobs == [
        ("ingest_item", item_id, {"generation": g, "_job_id": f"ingest:{item_id}:{g}"})
        for g in (0, 1)
    ]


def test_files_with_the_same_name_are_separate_items(
    client: TestClient, auth: dict[str, str], jobs: Jobs
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


def _save(client: TestClient, auth: dict[str, str]) -> str:
    url = f"https://example.com/page/{uuid.uuid4().hex[:8]}"
    resp = client.post("/v1/ingest", json={"url": url}, headers=auth)
    assert resp.status_code == 202, resp.text
    return resp.json()["item"]["id"]


def test_resending_an_upload_with_its_key_returns_the_first_item(
    client: TestClient, auth: dict[str, str], jobs: Jobs
) -> None:
    headers = {**auth, "Idempotency-Key": "share-1234"}
    first = client.post(
        "/v1/ingest/file", files={"file": ("a.png", PNG, "image/png")}, headers=headers
    )
    second = client.post(
        "/v1/ingest/file", files={"file": ("a.png", PNG, "image/png")}, headers=headers
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["item"]["id"] == first.json()["item"]["id"]
    item_id = first.json()["item"]["id"]
    assert jobs == [("ingest_file", item_id, {"generation": 0, "_job_id": f"ingest:{item_id}:0"})]


def test_pages_by_cursor_hold_when_the_cursor_item_is_deleted(
    client: TestClient, auth: dict[str, str], jobs: Jobs
) -> None:
    newest_first = [_save(client, auth) for _ in range(5)][::-1]
    first = client.get("/v1/items", params={"limit": 2}, headers=auth).json()
    assert [i["id"] for i in first] == newest_first[:2]
    assert client.delete(f"/v1/items/{first[-1]['id']}", headers=auth).status_code == 204
    rest = client.get(
        "/v1/items", params={"limit": 10, "before": first[-1]["id"]}, headers=auth
    ).json()
    assert [i["id"] for i in rest] == newest_first[2:]
    unknown = client.get("/v1/items", params={"before": str(uuid.uuid4())}, headers=auth)
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Unknown cursor"


def test_changes_lists_what_changed_and_what_was_deleted(
    client: TestClient, auth: dict[str, str], jobs: Jobs
) -> None:
    start = client.get("/v1/items/changes", headers=auth).json()
    assert start["full"] is True
    assert start["until"].endswith("Z")
    kept, gone = _save(client, auth), _save(client, auth)
    assert client.delete(f"/v1/items/{gone}", headers=auth).status_code == 204

    changes = client.get("/v1/items/changes", params={"since": start["until"]}, headers=auth).json()
    assert changes["full"] is False
    assert kept in changes["changed"]
    assert gone not in changes["changed"]
    assert changes["deleted"] == [gone]
    # A `since` without a timezone is taken as UTC.
    naive = client.get(
        "/v1/items/changes", params={"since": start["until"].removesuffix("Z")}, headers=auth
    )
    assert naive.status_code == 200
    assert naive.json()["deleted"] == [gone]
    # Deletions from that long ago aren't kept: walk the list.
    old = client.get("/v1/items/changes", params={"since": "2020-01-01T00:00:00Z"}, headers=auth)
    assert old.json()["full"] is True


def test_item_detail_answers_304_until_it_changes(
    client: TestClient, auth: dict[str, str], jobs: Jobs
) -> None:
    item_id = _save(client, auth)
    first = client.get(f"/v1/items/{item_id}", headers=auth)
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "private, no-cache"
    same = client.get(f"/v1/items/{item_id}", headers={**auth, "If-None-Match": etag})
    assert same.status_code == 304
    assert same.content == b""

    assert (
        client.patch(
            f"/v1/items/{item_id}", json={"share_text": "a note"}, headers=auth
        ).status_code
        == 200
    )
    changed = client.get(f"/v1/items/{item_id}", headers={**auth, "If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.json()["share_text"] == "a note"
    # Queued, not claimed: the reconciler fails items left "processing".
    assert changed.json()["status"] == "pending"
    # The edit queued generation 1; a job still running for 0 writes nothing.
    assert jobs[-1] == ("ingest_item", item_id, {"generation": 1, "_job_id": f"ingest:{item_id}:1"})


def test_bodies_over_the_cap_are_refused(client: TestClient, auth: dict[str, str]) -> None:
    from app.main import MAX_BODY_BYTES

    resp = client.post(
        "/v1/ingest",
        content=b" " * (MAX_BODY_BYTES + 1),
        headers={**auth, "Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_chunked_bodies_over_the_cap_are_413(client: TestClient, auth: dict[str, str]) -> None:
    from app.main import MAX_BODY_BYTES

    def chunks() -> Iterator[bytes]:
        for _ in range(MAX_BODY_BYTES // (1024 * 1024) + 2):
            yield b" " * (1024 * 1024)

    resp = client.post(
        "/v1/ingest", content=chunks(), headers={**auth, "Content-Type": "application/json"}
    )
    assert resp.status_code == 413


def test_uploads_over_5_mb_are_refused_before_they_are_stored(
    client: TestClient, auth: dict[str, str], jobs: Jobs
) -> None:
    from app.api.ingest import MAX_UPLOAD_BYTES

    assert MAX_UPLOAD_BYTES == 5 * 1024 * 1024
    # One byte over, which BodyLimit lets through for the route to refuse,
    # and well over, which BodyLimit refuses before reading it.
    details = []
    for size in (MAX_UPLOAD_BYTES + 1, 2 * MAX_UPLOAD_BYTES):
        big = PNG + bytes(size - len(PNG))
        resp = client.post(
            "/v1/ingest/file", files={"file": ("big.png", big, "image/png")}, headers=auth
        )
        assert resp.status_code == 413, resp.text
        details.append(resp.json()["detail"])
    assert details[0] == "Files up to 5 MB can be saved"
    assert details[1].startswith("Request body over")
    assert jobs == []
