"""Device registration for APNs push.

Run via `docker compose exec api pytest tests/test_devices.py`.
"""

import secrets
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "super-secret-pw-123"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _signup(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/auth/signup",
        json={"email": f"dev-{uuid.uuid4().hex[:10]}@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def two_users(client: TestClient) -> Iterator[tuple[dict[str, str], dict[str, str]]]:
    a = _signup(client)
    b = _signup(client)
    yield a, b
    client.delete("/auth/me", headers=a)
    client.delete("/auth/me", headers=b)


def _count(client: TestClient, auth: dict[str, str]) -> int:
    resp = client.get("/devices/count", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()["count"]


def test_register_apns_token(client: TestClient, two_users: tuple) -> None:
    a, _ = two_users
    body = {"push_token": secrets.token_hex(32), "apns_env": "sandbox", "platform": "ios"}
    resp = client.post("/devices/register", json=body, headers=a)
    assert resp.status_code == 200, resp.text
    assert _count(client, a) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"push_token": "ExponentPushToken[abc123]", "apns_env": "production"},
        {"push_token": "ABCDEF" * 11, "apns_env": "production"},
        {"push_token": secrets.token_hex(32), "apns_env": "development"},
        {"expo_push_token": secrets.token_hex(32)},
    ],
)
def test_register_rejects_bad_input(client: TestClient, two_users: tuple, body: dict) -> None:
    a, _ = two_users
    resp = client.post("/devices/register", json=body, headers=a)
    assert resp.status_code == 422, resp.text
    assert _count(client, a) == 0


def test_same_token_moves_to_the_new_user(client: TestClient, two_users: tuple) -> None:
    a, b = two_users
    token = secrets.token_hex(32)
    client.post("/devices/register", json={"push_token": token, "apns_env": "sandbox"}, headers=a)
    resp = client.post(
        "/devices/register", json={"push_token": token, "apns_env": "production"}, headers=b
    )
    assert resp.status_code == 200, resp.text
    assert _count(client, a) == 0
    assert _count(client, b) == 1
