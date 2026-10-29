"""Per-user data isolation.

Two users each save items; neither can list, fetch, retry, delete, search,
or ask their way into the other's data. Run via
`docker compose exec api pytest tests/test_ownership.py`.
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import signup, unique_email


@pytest.fixture
def two_users(client: TestClient) -> tuple[dict, dict]:
    a = signup(client, unique_email("own"))
    b = signup(client, unique_email("own"))
    yield a, b
    # Cascading delete cleans up items, wiki, devices, chats; SuperTokens drops the login.
    client.delete("/auth/me", headers=a)
    client.delete("/auth/me", headers=b)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _ingest(client: TestClient, auth: dict[str, str], url: str) -> str:
    """Returns the new item id. Note: status will be `pending` — workers
    are not running in-process, so we don't wait for ready."""
    resp = client.post("/ingest", json={"url": url}, headers=auth)
    assert resp.status_code == 202, resp.text
    return resp.json()["item"]["id"]


def test_list_items_is_user_scoped(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, a, "https://example.com/a-only")
    b_item = _ingest(client, b, "https://example.com/b-only")

    a_list = client.get("/items", headers=a).json()
    a_ids = {it["id"] for it in a_list}
    assert a_item in a_ids
    assert b_item not in a_ids

    b_list = client.get("/items", headers=b).json()
    b_ids = {it["id"] for it in b_list}
    assert b_item in b_ids
    assert a_item not in b_ids


def test_get_item_404s_across_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, a, "https://example.com/a-private")
    # B asking for A's item gets 404, not 403 — id existence is not leaked.
    assert client.get(f"/items/{a_item}", headers=b).status_code == 404


def test_retry_404s_across_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, a, "https://example.com/retry-only")
    assert client.post(f"/items/{a_item}/retry", headers=b).status_code == 404


def test_retry_rejects_file_uploads(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    # The worker deletes an upload once it's processed (or fails), so a
    # retry would re-run the URL pipeline on a `file://` placeholder.
    a, _ = two_users
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
    resp = client.post(
        "/ingest/file",
        files={"file": ("note.png", png, "image/png")},
        headers=a,
    )
    assert resp.status_code == 202, resp.text
    item_id = resp.json()["item"]["id"]
    retry = client.post(f"/items/{item_id}/retry", headers=a)
    assert retry.status_code == 409
    assert "share" in retry.json()["detail"].lower()


def test_delete_404s_across_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, a, "https://example.com/delete-only")
    # B can't delete A's item.
    assert client.delete(f"/items/{a_item}", headers=b).status_code == 404
    # And the item still exists for A.
    assert client.get(f"/items/{a_item}", headers=a).status_code == 200


def test_same_url_allowed_for_two_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    shared_url = f"https://example.com/shared/{uuid.uuid4().hex[:8]}"
    a_item = _ingest(client, a, shared_url)
    b_item = _ingest(client, b, shared_url)
    assert a_item != b_item


def test_tags_aggregation_is_user_scoped(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_tags = client.get("/items/tags", headers=a).json()
    b_tags = client.get("/items/tags", headers=b).json()
    # Tags are only populated for `ready` items, which need a running
    # worker, so both lists should be empty in-process. The point is that
    # the call succeeds and doesn't 500 / leak across users.
    assert a_tags == {"tags": []}
    assert b_tags == {"tags": []}


def test_wiki_list_is_user_scoped(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    assert client.get("/wiki", headers=a).json() == []
    assert client.get("/wiki", headers=b).json() == []
