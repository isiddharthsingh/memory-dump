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


def _me(client: TestClient, access: str) -> dict:
    resp = client.get("/auth/me", headers=_bearer(access))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_signup_keeps_the_phone_number_with_its_country_code(client: TestClient) -> None:
    access, _ = _signup(client, name="Tester", phone="+1 (415) 555-0132")
    assert _me(client, access)["phone"] == "+14155550132"
    client.delete("/auth/me", headers=_bearer(access))


def test_signup_with_an_empty_phone_keeps_none(client: TestClient) -> None:
    access, _ = _signup(client, phone="")
    assert _me(client, access)["phone"] is None
    client.delete("/auth/me", headers=_bearer(access))


@pytest.mark.parametrize(
    "phone",
    [
        "call me",
        "+12345",
        "415 555 0132",
        "+1 415 555 0132 ext 9",
        "+" + "1" * 16,
        "+0 7911 123456",
        "+٩١٩٨٧٦٥٤٣٢١٠",
    ],
)
def test_signup_bad_phone_is_a_field_error(client: TestClient, phone: str) -> None:
    resp = client.post("/auth/signup", json=form(unique_email(), phone=phone), headers=HEADER_MODE)
    assert resp.json()["status"] == "FIELD_ERROR"
    assert resp.json()["formFields"][0]["id"] == "phone"


def test_profile_edit_changes_the_name_and_phone(client: TestClient) -> None:
    email = unique_email()
    access, _ = _signup(client, email, name="Tester", phone="+14155550132")
    resp = client.patch(
        "/auth/me",
        json={"name": "  New Name ", "phone": "+91 98765 43210"},
        headers=_bearer(access),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "New Name"
    assert resp.json()["phone"] == "+919876543210"
    assert resp.json()["email"] == email
    assert _me(client, access) == resp.json()
    client.delete("/auth/me", headers=_bearer(access))


def test_profile_edit_leaves_what_it_does_not_name(client: TestClient) -> None:
    access, _ = _signup(client, name="Tester", phone="+14155550132")
    resp = client.patch("/auth/me", json={"name": "Renamed"}, headers=_bearer(access))
    assert resp.json()["display_name"] == "Renamed"
    assert resp.json()["phone"] == "+14155550132"
    resp = client.patch(
        "/auth/me", json={"name": None, "phone": "+447911123456"}, headers=_bearer(access)
    )
    assert resp.json()["display_name"] == "Renamed"
    assert resp.json()["phone"] == "+447911123456"
    # Nothing to change is not an error.
    assert client.patch("/auth/me", json={}, headers=_bearer(access)).json() == resp.json()
    client.delete("/auth/me", headers=_bearer(access))


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   "},
        {"name": "n" * 101},
        {"phone": "415 555 0132"},
        {"phone": "+0 7911 123456"},
        {"name": "Fine", "phone": "call me"},
    ],
)
def test_profile_edit_refuses_a_bad_name_or_phone(client: TestClient, body: dict) -> None:
    access, _ = _signup(client, name="Tester", phone="+14155550132")
    resp = client.patch("/auth/me", json=body, headers=_bearer(access))
    assert resp.status_code == 422, resp.text
    assert isinstance(resp.json()["detail"], str)
    # Neither field changed, the good one beside a bad one included.
    me = _me(client, access)
    assert (me["display_name"], me["phone"]) == ("Tester", "+14155550132")
    client.delete("/auth/me", headers=_bearer(access))


def test_profile_edit_needs_a_session_and_changes_only_its_own_account(client: TestClient) -> None:
    assert client.patch("/auth/me", json={"name": "Nobody"}).status_code == 401
    mine, _ = _signup(client, name="Mine", phone="+14155550132")
    theirs, _ = _signup(client, name="Theirs", phone="+14155550133")
    client.patch("/auth/me", json={"name": "Changed"}, headers=_bearer(mine))
    assert _me(client, theirs)["display_name"] == "Theirs"
    for access in (mine, theirs):
        client.delete("/auth/me", headers=_bearer(access))


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


def test_password_reset_and_email_lookup_are_not_served(client: TestClient) -> None:
    email = unique_email()
    access, _ = _signup(client, email)
    reset = client.post(
        "/auth/user/password/reset/token",
        json={"formFields": [{"id": "email", "value": email}]},
        headers=HEADER_MODE,
    )
    assert reset.status_code == 404
    applied = client.post(
        "/auth/user/password/reset",
        json={"formFields": [{"id": "password", "value": "x" * 12}], "token": "t"},
        headers=HEADER_MODE,
    )
    assert applied.status_code == 404
    for path in ("/auth/emailpassword/email/exists", "/auth/signup/email/exists"):
        assert client.get(path, params={"email": email}).status_code == 404, path
    client.delete("/auth/me", headers=_bearer(access))


def test_the_dashboard_is_off_unless_turned_on(client: TestClient) -> None:
    assert client.get("/auth/dashboard").status_code == 404
    assert client.post("/auth/dashboard/api/signin", json={}).status_code == 404


def test_sessions_are_header_based_even_when_a_cookie_is_asked_for(client: TestClient) -> None:
    email = unique_email()
    access, _ = _signup(client, email)
    resp = client.post(
        "/auth/signin", json=form(email, PASSWORD), headers={"st-auth-mode": "cookie"}
    )
    assert resp.json()["status"] == "OK"
    assert "st-access-token" in resp.headers
    assert "sAccessToken" not in resp.headers.get("set-cookie", "")
    client.delete("/auth/me", headers=_bearer(access))
