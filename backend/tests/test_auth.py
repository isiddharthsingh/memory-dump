"""Auth-flow integration tests.

These hit the real Postgres, Redis and SuperTokens core via TestClient; run
them with `docker compose exec api pytest tests/test_auth.py`. Each test uses a
fresh random email so collisions across runs are impossible. Requests send
`st-auth-mode: header` like the app, so tokens come back in response headers.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import PASSWORD, form, unique_email

HEADER_MODE = {"st-auth-mode": "header"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _tokens(resp) -> tuple[str, str]:
    return resp.headers["st-access-token"], resp.headers["st-refresh-token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signup(client: TestClient, email: str | None = None, **extra: str) -> tuple[str, str]:
    resp = client.post(
        "/auth/signup", json=form(email or unique_email(), **extra), headers=HEADER_MODE
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "OK", resp.text
    return _tokens(resp)


def _signin(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/auth/signin", json=form(email, password), headers=HEADER_MODE)


def _refresh(client: TestClient, refresh_token: str):
    return client.post("/auth/session/refresh", headers={**HEADER_MODE, **_bearer(refresh_token)})


def test_signup_creates_account_and_profile(client: TestClient) -> None:
    email = unique_email()
    access, _ = _signup(client, email, name="Tester")
    resp = client.get("/auth/me", headers=_bearer(access))
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == email
    assert resp.json()["display_name"] == "Tester"
    client.delete("/auth/me", headers=_bearer(access))


def test_signup_duplicate_email_is_a_field_error(client: TestClient) -> None:
    email = unique_email()
    access, _ = _signup(client, email)
    resp = client.post("/auth/signup", json=form(email), headers=HEADER_MODE)
    assert resp.json()["status"] == "FIELD_ERROR"
    assert resp.json()["formFields"][0]["id"] == "email"
    client.delete("/auth/me", headers=_bearer(access))


@pytest.mark.parametrize("password", ["short", "abcdefg1", "x" * 101])
def test_signup_password_outside_10_to_100_chars_is_a_field_error(
    client: TestClient, password: str
) -> None:
    resp = client.post("/auth/signup", json=form(unique_email(), password), headers=HEADER_MODE)
    assert resp.json()["status"] == "FIELD_ERROR"
    assert resp.json()["formFields"][0]["id"] == "password"


def test_signup_long_name_is_a_field_error(client: TestClient) -> None:
    resp = client.post(
        "/auth/signup", json=form(unique_email(), name="n" * 101), headers=HEADER_MODE
    )
    assert resp.json()["status"] == "FIELD_ERROR"
    assert resp.json()["formFields"][0]["id"] == "name"


def test_signup_normalizes_email_case(client: TestClient) -> None:
    email = unique_email("Foo").replace("example.com", "Example.COM")
    access, _ = _signup(client, email)
    assert client.get("/auth/me", headers=_bearer(access)).json()["email"] == email.lower()
    assert _signin(client, email.lower()).json()["status"] == "OK"
    client.delete("/auth/me", headers=_bearer(access))


def test_signin_happy_path(client: TestClient) -> None:
    email = unique_email()
    _, first_refresh = _signup(client, email)
    resp = _signin(client, email)
    assert resp.json()["status"] == "OK"
    access, refresh = _tokens(resp)
    assert refresh != first_refresh  # a second, separate session
    client.delete("/auth/me", headers=_bearer(access))


def test_signin_wrong_password(client: TestClient) -> None:
    email = unique_email()
    access, _ = _signup(client, email)
    assert _signin(client, email, "wrong-password-1").json()["status"] == "WRONG_CREDENTIALS_ERROR"
    client.delete("/auth/me", headers=_bearer(access))


def test_signin_unknown_email(client: TestClient) -> None:
    assert _signin(client, unique_email()).json()["status"] == "WRONG_CREDENTIALS_ERROR"


def test_refresh_rotates_and_detects_reuse(client: TestClient) -> None:
    email = unique_email()
    _, old_refresh = _signup(client, email)
    resp = _refresh(client, old_refresh)
    assert resp.status_code == 200, resp.text
    access, refresh = _tokens(resp)
    assert refresh != old_refresh
    # Using the new access token retires the old refresh token...
    assert client.get("/auth/me", headers=_bearer(access)).status_code == 200
    # ...so presenting it again looks like theft and ends the session.
    assert _refresh(client, old_refresh).status_code == 401
    assert _refresh(client, refresh).status_code == 401
    fresh, _ = _tokens(_signin(client, email))
    client.delete("/auth/me", headers=_bearer(fresh))


def test_signout_revokes_the_session(client: TestClient) -> None:
    access, refresh = _signup(client)
    resp = client.post("/auth/signout", headers={**HEADER_MODE, **_bearer(access)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "OK"
    assert _refresh(client, refresh).status_code == 401
    client.delete("/auth/me", headers=_bearer(access))


def test_me_requires_auth(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=_bearer("garbage")).status_code == 401


def test_delete_me_removes_login_and_data(client: TestClient) -> None:
    email = unique_email()
    access, refresh = _signup(client, email)
    assert client.delete("/auth/me", headers=_bearer(access)).status_code == 204
    # The access token is still unexpired, but the account behind it is gone.
    assert client.get("/auth/me", headers=_bearer(access)).status_code == 401
    assert _refresh(client, refresh).status_code == 401
    assert _signin(client, email).json()["status"] == "WRONG_CREDENTIALS_ERROR"
