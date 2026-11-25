"""Consent to the AI services (App Review 5.1.2(i)): until the user agrees,
nothing of theirs goes to a model, to Voyage, or to the services that fetch a
link for us; and they can withdraw it. Plus the profile's optional phone."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from app.ai import routing
from app.config import get_settings
from app.db.models import Item
from app.errors import ConsentRequired, user_message
from app.main import app
from app.workers import ingest
from app.workers.ingest import _reason
from tests.conftest import signup


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def newcomer(client: TestClient) -> Iterator[tuple[dict[str, str], uuid.UUID]]:
    """An account that hasn't agreed yet, as every new one starts."""
    headers = signup(client, consent=False)
    yield headers, uuid.UUID(client.get("/auth/me", headers=headers).json()["id"])
    client.delete("/auth/me", headers=headers)


def test_consent_starts_unset_and_can_be_given_and_withdrawn(
    client: TestClient, newcomer: tuple[dict[str, str], uuid.UUID]
) -> None:
    headers, _ = newcomer
    assert client.get("/v1/auth/me", headers=headers).json()["ai_consent_at"] is None

    given = client.put("/v1/auth/me/ai-consent", headers=headers)
    assert given.status_code == 200, given.text
    at = given.json()["ai_consent_at"]
    assert at is not None
    # Agreeing again keeps the first time.
    assert client.put("/v1/auth/me/ai-consent", headers=headers).json()["ai_consent_at"] == at
    assert client.get("/v1/auth/me", headers=headers).json()["ai_consent_at"] == at

    withdrawn = client.delete("/v1/auth/me/ai-consent", headers=headers)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["ai_consent_at"] is None
    assert client.get("/v1/auth/me", headers=headers).json()["ai_consent_at"] is None


def test_without_consent_ask_and_retry_refuse_before_anything_starts(
    client: TestClient, db: Any, newcomer: tuple[dict[str, str], uuid.UUID], jobs: Any
) -> None:
    headers, user_id = newcomer
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url="https://example.com/a",
                source="webpage",
                status="failed",
            )
        )
    for resp in (
        client.post("/v1/ask", json={"question": "anything?"}, headers=headers),
        client.post("/v1/ask/stream", json={"question": "anything?"}, headers=headers),
        client.post(f"/v1/items/{item_id}/retry", headers=headers),
    ):
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == str(ConsentRequired())
    assert jobs == []
    # A save is still taken, as with the spend limit: the share extension
    # drops a share on any 4xx. It fails with the reason, and nothing more.
    saved = client.post("/v1/ingest", json={"url": "https://example.com/b"}, headers=headers)
    assert saved.status_code == 202, saved.text


def test_without_consent_a_save_fails_before_its_link_is_fetched(
    client: TestClient,
    db: Any,
    newcomer: tuple[dict[str, str], uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = newcomer
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url="https://x.com/someone/status/1",
                source="twitter",
                status="pending",
            )
        )

    def never(*_: Any, **__: Any) -> Any:
        raise AssertionError("worked on without consent")

    # Not even fetched: FxTwitter and Jina are third parties too.
    monkeypatch.setattr(ingest, "get_extractor", never)
    monkeypatch.setattr(ingest, "analyze", never)
    outcome = client.portal.call(ingest.ingest_item, {"job_try": 1}, str(item_id))  # type: ignore[union-attr]
    assert outcome == "failed"
    with db.connect() as conn:
        error = conn.execute(select(Item.error).where(Item.id == item_id)).scalar_one()
    assert error == f"ConsentRequired: {ConsentRequired()}"


def test_without_consent_search_answers_from_keywords_alone(
    client: TestClient, newcomer: tuple[dict[str, str], uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, _ = newcomer
    sent: list[str] = []

    def voyage(*_: Any, **__: Any) -> Any:
        # Search would swallow this and fall back, so it's counted instead.
        sent.append("voyage")
        raise AssertionError("sent to Voyage without consent")

    from app.ai import clients

    monkeypatch.setattr(clients, "_voyage", voyage)
    resp = client.post("/v1/search", json={"q": "ramen"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert sent == []


def test_every_model_and_voyage_call_is_refused_without_consent(
    client: TestClient, newcomer: tuple[dict[str, str], uuid.UUID]
) -> None:
    import structlog

    _, user_id = newcomer

    async def routes() -> None:
        with structlog.contextvars.bound_contextvars(user_id=str(user_id)):
            with pytest.raises(ConsentRequired):
                await routing.resolve()
            with pytest.raises(ConsentRequired):
                await routing.voyage()
            # The server's own work on their rows isn't asked (EXEMPT_JOBS).
            with structlog.contextvars.bound_contextvars(job="forget_item"):
                assert await routing.resolve() == routing.PLATFORM

    # On the app's own loop, where its database pool lives.
    client.portal.call(routes)  # type: ignore[union-attr]


def test_the_refusal_reads_as_a_reason_the_app_shows() -> None:
    refusal = ConsentRequired()
    assert user_message(refusal) == str(refusal)
    assert _reason(refusal) == f"ConsentRequired: {refusal}"
    assert "Allow AI processing" in str(refusal)


def test_the_phone_is_optional_and_can_be_removed(client: TestClient) -> None:
    headers = signup(client, phone="")
    try:
        assert client.get("/v1/auth/me", headers=headers).json()["phone"] is None
        added = client.patch("/v1/auth/me", json={"phone": "+1 415 555 0100"}, headers=headers)
        assert added.status_code == 200, added.text
        assert added.json()["phone"] == "+14155550100"
        # Null leaves it; empty takes it away.
        kept = client.patch("/v1/auth/me", json={"phone": None, "name": "Sam"}, headers=headers)
        assert kept.json()["phone"] == "+14155550100"
        removed = client.patch("/v1/auth/me", json={"phone": ""}, headers=headers)
        assert removed.status_code == 200, removed.text
        assert removed.json()["phone"] is None
        assert removed.json()["display_name"] == "Sam"
        bad = client.patch("/v1/auth/me", json={"phone": "12"}, headers=headers)
        assert bad.status_code == 422
    finally:
        client.delete("/auth/me", headers=headers)


@pytest.mark.parametrize("path", ["/privacy", "/terms", "/support"])
def test_the_legal_pages_are_public_and_name_who_to_contact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(get_settings(), "operator_name", "Sam <Example>")
    monkeypatch.setattr(get_settings(), "support_email", "help@example.com")
    # Open to every build, like the health checks: a browser sends none.
    monkeypatch.setattr(get_settings(), "min_app_build", 99)
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    assert "mailto:help@example.com" in resp.text
    if path != "/support":
        assert "Sam &lt;Example&gt;" in resp.text
    # Not under /v1: these are pages, not the API.
    assert client.get(f"/v1{path}").status_code == 404


def test_the_privacy_policy_names_every_service_the_consent_screen_does(
    client: TestClient,
) -> None:
    text = client.get("/privacy").text
    for service in (
        "Anthropic",
        "Voyage AI",
        "OpenAI",
        "Google",
        "OpenRouter",
        "Bedrock",
        "FxTwitter",
        "Jina AI",
        "Apple",
    ):
        assert service in text, service
