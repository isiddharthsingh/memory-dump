"""Auth-flow integration tests.

These hit the real Postgres + Redis via TestClient; run them with
`docker compose exec api pytest tests/test_auth.py`. Each test uses a
fresh random email so collisions across runs are impossible.
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "super-secret-pw-123"


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _signup(client: TestClient, email: str | None = None) -> dict:
    email = email or _unique_email()
    resp = client.post(
        "/auth/signup",
        json={"email": email, "password": PASSWORD, "display_name": "Tester"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["email"] == email.lower()
    return body


def test_signup_happy_path(client: TestClient) -> None:
    body = _signup(client)
    # Cleanup so we don't leak rows across runs.
    client.delete("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})


def test_signup_duplicate_email_conflicts(client: TestClient) -> None:
    email = _unique_email()
    first = _signup(client, email)
    resp = client.post(
        "/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 409
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"}
    )


def test_signup_short_password_rejected(client: TestClient) -> None:
    resp = client.post(
        "/auth/signup",
        json={"email": _unique_email(), "password": "short"},
    )
    # Pydantic Field(min_length=10) → 422.
    assert resp.status_code == 422


def test_signup_normalizes_email_case(client: TestClient) -> None:
    upper = f"Foo-{uuid.uuid4().hex[:6]}@Example.COM"
    body = _signup(client, upper)
    assert body["user"]["email"] == upper.lower()
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )


def test_login_happy_path(client: TestClient) -> None:
    body = _signup(client)
    email = body["user"]["email"]
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    login_body = resp.json()
    assert login_body["access_token"]
    assert login_body["refresh_token"] != body["refresh_token"]  # fresh session
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {login_body['access_token']}"}
    )


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    body = _signup(client)
    email = body["user"]["email"]
    resp = client.post("/auth/login", json={"email": email, "password": "wrong-password-xx"})
    assert resp.status_code == 401
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )


def test_login_unknown_email_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/auth/login", json={"email": _unique_email(), "password": PASSWORD}
    )
    assert resp.status_code == 401


def test_refresh_rotates_tokens(client: TestClient) -> None:
    body = _signup(client)
    refresh = body["refresh_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    rotated = resp.json()
    assert rotated["refresh_token"] != refresh
    # Access JWTs carry only second-resolution claims, so one minted in the
    # same second as the signup token is byte-identical — just check it exists.
    assert rotated["access_token"]
    # Old refresh is now revoked — using it again triggers reuse detection.
    reuse = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert reuse.status_code == 401
    # And reuse detection should have revoked the new refresh too.
    after_reuse = client.post(
        "/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert after_reuse.status_code == 401
    # Cleanup via login → delete.
    login = client.post(
        "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
    ).json()
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {login['access_token']}"}
    )


def test_logout_invalidates_refresh(client: TestClient) -> None:
    body = _signup(client)
    resp = client.post("/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert resp.status_code == 204
    # Subsequent refresh attempt with the same token should 401.
    after = client.post(
        "/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert after.status_code == 401
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )


def test_me_requires_auth(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code
        == 401
    )


def test_me_returns_current_user(client: TestClient) -> None:
    body = _signup(client)
    resp = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == body["user"]["email"]
    client.delete(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )


def test_delete_me_invalidates_token(client: TestClient) -> None:
    body = _signup(client)
    auth = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.delete("/auth/me", headers=auth).status_code == 204
    # Same token now references a deleted user.
    assert client.get("/auth/me", headers=auth).status_code == 401
