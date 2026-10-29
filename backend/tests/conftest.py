"""Shared pytest setup.

Tests run inside the compose stack (`docker compose exec api pytest`), against
the real Postgres, Redis and SuperTokens core. The helpers sign up throwaway
accounts the way the app does: SuperTokens' /auth/signup, header-based tokens.
"""

import uuid

from fastapi.testclient import TestClient

PASSWORD = "super-secret-pw-123"


def unique_email(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def form(email: str, password: str = PASSWORD, **extra: str) -> dict:
    fields = {"email": email, "password": password, **extra}
    return {"formFields": [{"id": k, "value": v} for k, v in fields.items()]}


def signup(client: TestClient, email: str | None = None, **extra: str) -> dict[str, str]:
    """Signs up a fresh user; returns the Authorization header for its session."""
    resp = client.post(
        "/auth/signup",
        json=form(email or unique_email(), **extra),
        headers={"st-auth-mode": "header"},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "OK", resp.text
    return {"Authorization": f"Bearer {resp.headers['st-access-token']}"}
