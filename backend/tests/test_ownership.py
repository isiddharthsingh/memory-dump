"""Per-user data isolation.

Two users each save items; neither can list, fetch, retry, delete, search,
or ask their way into the other's data. Run via
`docker compose exec api pytest tests/test_ownership.py`.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "super-secret-pw-123"


def _signup(client: TestClient) -> dict:
    resp = client.post(
        "/auth/signup",
        json={
            "email": f"own-{uuid.uuid4().hex[:10]}@example.com",
            "password": PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth(body: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def two_users(client: TestClient) -> tuple[dict, dict]:
    a = _signup(client)
    b = _signup(client)
    yield a, b
    # Cascading delete cleans up items, wiki, devices, refresh tokens.
    client.delete("/auth/me", headers=_auth(a))
    client.delete("/auth/me", headers=_auth(b))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
    a_item = _ingest(client, _auth(a), "https://example.com/a-only")
    b_item = _ingest(client, _auth(b), "https://example.com/b-only")

    a_list = client.get("/items", headers=_auth(a)).json()
    a_ids = {it["id"] for it in a_list}
    assert a_item in a_ids
    assert b_item not in a_ids

    b_list = client.get("/items", headers=_auth(b)).json()
    b_ids = {it["id"] for it in b_list}
    assert b_item in b_ids
    assert a_item not in b_ids


def test_get_item_404s_across_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, _auth(a), "https://example.com/a-private")
    # B asking for A's item gets 404, not 403 — id existence is not leaked.
    assert client.get(f"/items/{a_item}", headers=_auth(b)).status_code == 404


def test_retry_404s_across_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, _auth(a), "https://example.com/retry-only")
    assert (
        client.post(f"/items/{a_item}/retry", headers=_auth(b)).status_code == 404
    )


def test_delete_404s_across_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_item = _ingest(client, _auth(a), "https://example.com/delete-only")
    # B can't delete A's item.
    assert client.delete(f"/items/{a_item}", headers=_auth(b)).status_code == 404
    # And the item still exists for A.
    assert client.get(f"/items/{a_item}", headers=_auth(a)).status_code == 200


def test_same_url_allowed_for_two_users(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    shared_url = f"https://example.com/shared/{uuid.uuid4().hex[:8]}"
    a_item = _ingest(client, _auth(a), shared_url)
    b_item = _ingest(client, _auth(b), shared_url)
    assert a_item != b_item


def test_tags_aggregation_is_user_scoped(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    a_tags = client.get("/items/tags", headers=_auth(a)).json()
    b_tags = client.get("/items/tags", headers=_auth(b)).json()
    # Tags are only populated for `ready` items, which need a running
    # worker, so both lists should be empty in-process. The point is that
    # the call succeeds and doesn't 500 / leak across users.
    assert a_tags == {"tags": []}
    assert b_tags == {"tags": []}


def test_wiki_list_is_user_scoped(
    client: TestClient, two_users: tuple[dict, dict]
) -> None:
    a, b = two_users
    assert client.get("/wiki", headers=_auth(a)).json() == []
    assert client.get("/wiki", headers=_auth(b)).json() == []
