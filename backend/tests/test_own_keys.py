"""A user's own provider keys, the month's free allowance, and their own
spend limit: what is stored, where a call is routed, and when it is refused.
No provider is called; the key check and the adapters are stubbed."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, insert, select, update
from voyageai import error as voyage_error

from app import pricing, secretbox
from app.ai import catalog, clients, routing
from app.ai import synthesize as synthesize_ai
from app.ai.wire import Message, ProviderError, Text, Usage
from app.config import get_settings
from app.db.models import Item, LlmUsage, ProviderKey, User
from app.errors import SpendLimitReached, is_transient, user_message
from app.main import app
from app.workers.ingest import _reason
from tests.conftest import signup

KEY = "sk-test-0123456789abcdWXYZ"
VOYAGE_KEY = "pa-test-0123456789abcdVOYG"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "keys_secret", "test-secret-" + "x" * 32)
    monkeypatch.setattr(get_settings(), "free_monthly_usd", 3.0)
    monkeypatch.setattr(get_settings(), "voyage_free_monthly_usd", 1.0)
    monkeypatch.setattr(get_settings(), "rate_limit_keys_per_hour", 0)


@pytest.fixture
def account(client: TestClient) -> Iterator[tuple[dict[str, str], uuid.UUID]]:
    headers = signup(client)
    yield headers, uuid.UUID(client.get("/auth/me", headers=headers).json()["id"])
    client.delete("/auth/me", headers=headers)


@pytest.fixture
def accepted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str | None]]:
    """Every key passes the provider's check; returns what was checked."""
    checked: list[tuple[str, str, str | None]] = []

    async def check_key(provider: str, key: str, region: str | None = None) -> None:
        checked.append((provider, key, region))

    monkeypatch.setattr(clients, "check_key", check_key)
    return checked


def _spend(
    db: Any, user_id: uuid.UUID, usd: str, billed_to: str = "platform", model: str | None = None
) -> None:
    with db.begin() as conn:
        conn.execute(
            insert(LlmUsage.__table__).values(
                user_id=user_id,
                day=func.current_date(),
                model=model or f"spent-{uuid.uuid4().hex[:6]}",
                billed_to=billed_to,
                calls=1,
                cost_usd=Decimal(usd),
            )
        )


def _as_user[T](client: TestClient, user_id: uuid.UUID, call: Callable[[], Awaitable[T]]) -> T:
    """Runs `call` on the app's event loop with the user bound, as a request
    or a job of theirs would."""

    async def run() -> T:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(user_id=str(user_id))
        try:
            return await call()
        finally:
            structlog.contextvars.clear_contextvars()

    return client.portal.call(run)  # type: ignore[union-attr]


# --- prices ---------------------------------------------------------------


def test_a_call_costs_its_tokens_at_the_list_price() -> None:
    cost = pricing.cost(
        "anthropic",
        "claude-sonnet-5-5",
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    # $2 in, $1 out, $0.10 read from the cache, $2.50 written to it.
    assert cost == Decimal("5.6")
    assert pricing.cost("voyage", "voyage-3-large", input_tokens=1_000_000) == Decimal("0.18")
    # What the provider says it billed wins over the table.
    assert pricing.cost("openrouter", "x/unknown", input_tokens=5, billed=0.0123) == Decimal(
        "0.0123"
    )


def test_a_dated_or_unknown_model_is_never_free() -> None:
    assert pricing.price("anthropic", "claude-haiku-4-5-20251001") == pricing.price(
        "anthropic", "claude-haiku-4-5"
    )
    assert pricing.price("anthropic", "claude-opus-5-5-20260901").input == 4
    assert pricing.price("anthropic", "some-new-model").input == 10
    assert pricing.price("voyage", "voyage-9").input == 0.18
    # Bedrock's in-region endpoint charges a tenth more.
    assert pricing.price("bedrock", "anthropic.claude-haiku-5-5").output == pytest.approx(0.55)


def test_every_model_a_user_can_pick_has_a_price() -> None:
    for provider in catalog.PROVIDERS.values():
        offered = {m.id for m in provider.models}
        assert set(provider.defaults.values()) <= offered
        table = pricing._TABLES[provider.id]
        for model in offered:
            assert model.split("anthropic.", 1)[-1] in table or model in table, model


def test_a_pick_the_provider_no_longer_offers_falls_back_to_its_default() -> None:
    picked = {"openai": {"analysis": "gpt-5.4-mini", "ask": "gpt-2"}}
    assert catalog.chosen("openai", picked) == {
        "analysis": "gpt-5.4-mini",
        "synthesis": "gpt-6.1-sol",
        "ask": "gpt-6.1-sol",
    }
    assert catalog.chosen("gemini", picked) == catalog.PROVIDERS["gemini"].defaults


# --- sealing --------------------------------------------------------------


def test_a_sealed_key_opens_only_with_the_same_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = secretbox.seal(KEY)
    assert KEY not in sealed
    assert secretbox.unseal(sealed) == KEY
    monkeypatch.setattr(get_settings(), "keys_secret", "another-secret-" + "y" * 32)
    with pytest.raises(secretbox.Unreadable):
        secretbox.unseal(sealed)
    monkeypatch.setattr(get_settings(), "keys_secret", "short")
    assert not secretbox.available()


# --- the settings routes --------------------------------------------------


def test_a_new_account_is_on_the_free_allowance(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID]
) -> None:
    headers, _ = account
    body = client.get("/v1/ai", headers=headers).json()
    assert body["admin"] is False and body["keys"] is True and body["provider"] is None
    assert body["free"]["limit"] == 3.0 and body["free"]["spent"] == 0
    assert date.fromisoformat(body["free"]["resets"]) == routing.resets()
    assert body["own"] == {"limit": None, "spent": 0}
    # Voyage counts toward the free allowance: no key, and no cap of its own.
    assert body["voyage"] == {
        "id": "voyage",
        "name": "Voyage",
        "placeholder": "pa-…",
        "help": "Create a key at dashboard.voyageai.com under API keys.",
        "key": None,
        "limit": None,
        "spent": 0,
    }
    assert [p["id"] for p in body["providers"]] == [
        "anthropic",
        "openai",
        "gemini",
        "openrouter",
        "bedrock",
    ]
    for provider in body["providers"]:
        assert provider["key"] is None
        assert set(provider["chosen"]) == {"analysis", "synthesis", "ask"}
        assert set(provider["chosen"].values()) <= {m["id"] for m in provider["models"]}
    assert client.get("/v1/ai").status_code == 401


def test_a_saved_key_is_checked_sealed_put_in_use_and_never_returned(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
) -> None:
    headers, user_id = account
    resp = client.put("/v1/ai/keys/openai", json={"key": f"  {KEY}\n"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert accepted == [("openai", KEY, None)]
    assert KEY not in resp.text
    body = resp.json()
    assert body["provider"] == "openai"
    openai = next(p for p in body["providers"] if p["id"] == "openai")
    assert openai["key"]["hint"] == "WXYZ" and openai["key"]["region"] is None

    with db.connect() as conn:
        row = conn.execute(
            select(ProviderKey.secret, ProviderKey.hint).where(ProviderKey.user_id == user_id)
        ).one()
    assert KEY not in row.secret and secretbox.unseal(row.secret) == KEY

    # Bedrock's key comes with a region, us-east-1 unless told.
    assert (
        client.put("/v1/ai/keys/bedrock", json={"key": KEY}, headers=headers).json()["provider"]
        == "bedrock"
    )
    resp = client.put(
        "/v1/ai/keys/bedrock", json={"key": KEY, "region": "eu-west-1"}, headers=headers
    )
    bedrock = next(p for p in resp.json()["providers"] if p["id"] == "bedrock")
    assert bedrock["key"]["region"] == "eu-west-1"
    assert [region for provider, _, region in accepted if provider == "bedrock"] == [
        "us-east-1",
        "eu-west-1",
    ]
    bad = client.put(
        "/v1/ai/keys/bedrock", json={"key": KEY, "region": "evil.example.com"}, headers=headers
    )
    assert bad.status_code == 422

    # Another account sees none of it.
    other = signup(client)
    try:
        theirs = client.get("/v1/ai", headers=other).json()
        assert theirs["provider"] is None
        assert all(p["key"] is None for p in theirs["providers"])
    finally:
        client.delete("/auth/me", headers=other)


def test_a_key_the_provider_turns_down_is_not_saved(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    answers = iter([401, 503, 400])

    async def check_key(provider: str, key: str, region: str | None = None) -> None:
        raise ProviderError(provider, next(answers), "invalid x-api-key sk-secret", 7.0)

    monkeypatch.setattr(clients, "check_key", check_key)
    refused = client.put("/v1/ai/keys/anthropic", json={"key": KEY}, headers=headers)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Anthropic didn't accept that key."
    down = client.put("/v1/ai/keys/anthropic", json={"key": KEY}, headers=headers)
    assert down.status_code == 503 and down.headers["Retry-After"] == "7"
    odd = client.put("/v1/ai/keys/bedrock", json={"key": KEY}, headers=headers)
    assert odd.status_code == 422 and "region" in odd.json()["detail"]
    # Never the provider's own words.
    assert "sk-secret" not in refused.text + down.text + odd.text

    assert client.put("/v1/ai/keys/nope", json={"key": KEY}, headers=headers).status_code == 404
    assert (
        client.put("/v1/ai/keys/openai", json={"key": "a b c d e f"}, headers=headers).status_code
        == 422
    )
    with db.connect() as conn:
        assert conn.scalar(select(func.count()).where(ProviderKey.user_id == user_id)) == 0


def test_no_keys_are_taken_without_a_secret(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, _ = account
    monkeypatch.setattr(get_settings(), "keys_secret", "")
    assert client.get("/v1/ai", headers=headers).json()["keys"] is False
    assert client.put("/v1/ai/keys/openai", json={"key": KEY}, headers=headers).status_code == 503
    assert accepted == []


def test_choosing_the_provider_the_models_and_a_limit(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
) -> None:
    headers, _ = account
    none = client.put("/v1/ai/provider", json={"provider": "gemini"}, headers=headers)
    assert none.status_code == 422 and "Google Gemini" in none.json()["detail"]

    client.put("/v1/ai/keys/gemini", json={"key": KEY}, headers=headers)
    client.put("/v1/ai/keys/openai", json={"key": KEY}, headers=headers)
    assert (
        client.put("/v1/ai/provider", json={"provider": None}, headers=headers).json()["provider"]
        is None
    )
    assert (
        client.put("/v1/ai/provider", json={"provider": "gemini"}, headers=headers).json()[
            "provider"
        ]
        == "gemini"
    )

    picks = {
        "analysis": "gemini-3.8-flash",
        "synthesis": "gemini-3.8-flash",
        "ask": "gemini-3.1-pro-preview",
    }
    body = client.put("/v1/ai/models/gemini", json=picks, headers=headers).json()
    assert next(p for p in body["providers"] if p["id"] == "gemini")["chosen"] == picks
    # Picks for one provider leave another's alone.
    client.put(
        "/v1/ai/models/openai",
        json={"analysis": "gpt-5.4-mini", "synthesis": "gpt-6.1-sol", "ask": "gpt-6-astra"},
        headers=headers,
    )
    body = client.get("/v1/ai", headers=headers).json()
    assert next(p for p in body["providers"] if p["id"] == "gemini")["chosen"] == picks
    wrong = client.put(
        "/v1/ai/models/gemini", json={**picks, "ask": "gpt-6-astra"}, headers=headers
    )
    assert wrong.status_code == 422

    assert (
        client.put("/v1/ai/limit", json={"limit": 12.5}, headers=headers).json()["own"]["limit"]
        == 12.5
    )
    assert client.put("/v1/ai/limit", json={"limit": 0}, headers=headers).status_code == 422
    assert (
        client.put("/v1/ai/limit", json={"limit": None}, headers=headers).json()["own"]["limit"]
        is None
    )

    # Removing the key in use puts the account back on the server's models.
    body = client.delete("/v1/ai/keys/gemini", headers=headers).json()
    assert body["provider"] is None
    assert next(p for p in body["providers"] if p["id"] == "gemini")["key"] is None
    assert next(p for p in body["providers"] if p["id"] == "openai")["key"] is not None


# --- routing and the limits -----------------------------------------------


def test_the_free_allowance_ends_at_its_limit_but_not_for_an_admin(
    client: TestClient, db: Any, account: tuple[dict[str, str], uuid.UUID]
) -> None:
    headers, user_id = account
    _spend(db, user_id, "2.99")
    assert _as_user(client, user_id, routing.resolve) == routing.PLATFORM
    assert _as_user(client, user_id, routing.voyage) == routing.PLATFORM_VOYAGE

    _spend(db, user_id, "0.02")
    with pytest.raises(SpendLimitReached) as reached:
        _as_user(client, user_id, routing.resolve)
    with pytest.raises(SpendLimitReached) as embedding:
        _as_user(client, user_id, routing.voyage)
    assert str(embedding.value) == str(reached.value)
    assert not reached.value.own and not is_transient(reached.value)
    day = routing.resets()
    assert user_message(reached.value) == (
        "You've used this month's free $3.00. Add your own API key in Settings, "
        f"or wait until {day:%B} {day.day}."
    )
    body = client.get("/v1/ai", headers=headers).json()
    assert body["free"] == {"limit": 3.0, "spent": 3.01, "resets": day.isoformat()}

    # The operator's own runs over every account aren't capped.
    async def reembed() -> routing.Route:
        structlog.contextvars.bind_contextvars(job="reembed")
        assert await routing.voyage() == routing.PLATFORM_VOYAGE
        return await routing.resolve()

    assert _as_user(client, user_id, reembed) == routing.PLATFORM

    with db.begin() as conn:
        conn.execute(update(User.__table__).where(User.id == user_id).values(is_admin=True))
    assert _as_user(client, user_id, routing.resolve) == routing.PLATFORM
    assert _as_user(client, user_id, routing.voyage) == routing.PLATFORM_VOYAGE
    body = client.get("/v1/ai", headers=headers).json()
    assert body["admin"] is True and body["free"]["limit"] is None


def test_last_months_spend_and_a_server_without_a_cap_dont_count(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    with db.begin() as conn:
        conn.execute(
            insert(LlmUsage.__table__).values(
                user_id=user_id,
                day=func.date_trunc("month", func.current_date()) - func.make_interval(0, 0, 0, 1),
                model="last-month",
                billed_to="platform",
                cost_usd=Decimal("50"),
            )
        )
    assert _as_user(client, user_id, routing.resolve) == routing.PLATFORM
    _spend(db, user_id, "40")
    monkeypatch.setattr(get_settings(), "free_monthly_usd", 0)
    assert _as_user(client, user_id, routing.resolve) == routing.PLATFORM
    assert routing.resets(date(2026, 12, 31)) == date(2027, 1, 1)
    assert routing.resets(date(2026, 11, 19)) == date(2026, 12, 1)


def test_an_own_key_takes_the_calls_past_the_free_allowance_up_to_its_own_limit(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
) -> None:
    headers, user_id = account
    _spend(db, user_id, "5")
    client.put("/v1/ai/keys/openrouter", json={"key": KEY}, headers=headers)

    route = _as_user(client, user_id, routing.resolve)
    assert route.own and route.provider == "openrouter" and route.key == KEY
    assert route.user_id == user_id
    assert route.models == catalog.PROVIDERS["openrouter"].defaults
    # Embeddings stay on the server's key: what was spent there before the
    # key isn't Voyage's, so it doesn't count toward their month of it.
    assert _as_user(client, user_id, routing.voyage) == routing.PLATFORM_VOYAGE

    client.put("/v1/ai/limit", json={"limit": 2}, headers=headers)
    _spend(db, user_id, "1.50", "own")
    assert _as_user(client, user_id, routing.resolve).own
    _spend(db, user_id, "0.50", "own")
    with pytest.raises(SpendLimitReached) as reached:
        _as_user(client, user_id, routing.resolve)
    assert reached.value.own and "$2.00 monthly limit you set" in str(reached.value)
    body = client.get("/v1/ai", headers=headers).json()
    assert body["own"] == {"limit": 2.0, "spent": 2.0} and body["free"]["spent"] == 5.0

    # Back on the server's models, the used-up allowance stops them again.
    client.put("/v1/ai/provider", json={"provider": None}, headers=headers)
    with pytest.raises(SpendLimitReached) as free:
        _as_user(client, user_id, routing.resolve)
    assert not free.value.own


def test_on_an_own_key_the_servers_voyage_is_free_up_to_its_own_monthly_cap(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
) -> None:
    headers, user_id = account
    client.put("/v1/ai/keys/gemini", json={"key": KEY}, headers=headers)
    # Only Voyage's models count toward it, whatever else the server's keys did.
    _spend(db, user_id, "40")
    _spend(db, user_id, "0.99", model="voyage-3-large")
    assert _as_user(client, user_id, routing.voyage) == routing.PLATFORM_VOYAGE
    voyage = client.get("/v1/ai", headers=headers).json()["voyage"]
    assert voyage["key"] is None and voyage["limit"] == 1.0 and voyage["spent"] == 0.99

    _spend(db, user_id, "0.01", model="rerank-2.5")
    with pytest.raises(SpendLimitReached) as reached:
        _as_user(client, user_id, routing.voyage)
    assert reached.value.voyage and not reached.value.own and not is_transient(reached.value)
    day = routing.resets()
    assert user_message(reached.value) == (
        "You've used this month's free $1.00 of Voyage, which indexes and searches your "
        f"saves. Add your own Voyage key in Settings, or wait until {day:%B} {day.day}."
    )
    # Their models are on their own key and go on.
    assert _as_user(client, user_id, routing.resolve).own

    # Last month's doesn't count, a server without the cap has none, nor an admin.
    with db.begin() as conn:
        conn.execute(
            update(LlmUsage.__table__)
            .where(LlmUsage.user_id == user_id, LlmUsage.model == "voyage-3-large")
            .values(day=func.date_trunc("month", func.current_date()) - func.make_interval(0, 1))
        )
    assert _as_user(client, user_id, routing.voyage) == routing.PLATFORM_VOYAGE
    _spend(db, user_id, "5", model="voyage-3-large")
    with pytest.raises(SpendLimitReached):
        _as_user(client, user_id, routing.voyage)
    with db.begin() as conn:
        conn.execute(update(User.__table__).where(User.id == user_id).values(is_admin=True))
    assert _as_user(client, user_id, routing.voyage) == routing.PLATFORM_VOYAGE
    assert client.get("/v1/ai", headers=headers).json()["voyage"]["limit"] is None


class _OwnVoyage:
    """Stands in for a Voyage client made with a user's key."""

    keys: list[str] = []
    failure: Exception | None = None

    def __init__(self, api_key: str, **_: Any) -> None:
        self.keys.append(api_key)

    async def embed(self, texts: list[str], **_: Any) -> SimpleNamespace:
        if self.failure:
            raise self.failure
        return SimpleNamespace(embeddings=[[0.0] for _ in texts], total_tokens=1_000_000)

    async def rerank(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(results=[], total_tokens=1_000_000)


@pytest.fixture
def own_voyage(monkeypatch: pytest.MonkeyPatch) -> type[_OwnVoyage]:
    """Voyage clients made for a call are stand-ins, and the server's own
    Voyage client must not be used."""

    def server() -> None:
        raise AssertionError("the server's Voyage key was used")

    monkeypatch.setattr(_OwnVoyage, "keys", [])
    monkeypatch.setattr(_OwnVoyage, "failure", None)
    monkeypatch.setattr(clients.voyageai, "AsyncClient", _OwnVoyage)
    monkeypatch.setattr(clients, "voyage_client", server)
    return _OwnVoyage


def test_a_voyage_key_of_their_own_makes_their_voyage_calls_beside_the_provider_in_use(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
    own_voyage: type[_OwnVoyage],
) -> None:
    headers, user_id = account
    client.put("/v1/ai/keys/anthropic", json={"key": KEY}, headers=headers)
    _spend(db, user_id, "2", model="voyage-3-large")
    with pytest.raises(SpendLimitReached):
        _as_user(client, user_id, routing.voyage)

    saved = client.put("/v1/ai/keys/voyage", json={"key": VOYAGE_KEY}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert accepted[-1] == ("voyage", VOYAGE_KEY, None)
    body = saved.json()
    # Saved beside the provider's key, never in its place, and never returned.
    assert body["provider"] == "anthropic"
    assert "voyage" not in [p["id"] for p in body["providers"]]
    assert body["voyage"]["key"]["hint"] == "VOYG" and body["voyage"]["limit"] is None
    assert VOYAGE_KEY not in saved.text
    with db.connect() as conn:
        stored = conn.execute(
            select(ProviderKey.secret).where(
                ProviderKey.user_id == user_id, ProviderKey.provider == "voyage"
            )
        ).scalar_one()
    assert VOYAGE_KEY not in stored and secretbox.unseal(stored) == VOYAGE_KEY

    route = _as_user(client, user_id, routing.voyage)
    assert route == routing.Route(provider="voyage", own=True, key=VOYAGE_KEY, user_id=user_id)

    async def calls() -> None:
        await clients.voyage_embed(["a", "b"], input_type="document")
        await clients.voyage_rerank("q", ["d"], 1)

    _as_user(client, user_id, calls)
    assert own_voyage.keys == [VOYAGE_KEY, VOYAGE_KEY]
    # Billed to them: $0.18 of embedding and $0.05 of reranking.
    assert [row for row in _usage(db, user_id) if row.billed_to == "own"] == [
        ("rerank-2.5", "own", Decimal("0.05000000"), 1),
        ("voyage-3-large", "own", Decimal("0.18000000"), 1),
    ]
    body = client.get("/v1/ai", headers=headers).json()
    assert body["own"]["spent"] == 0.23 and body["voyage"]["spent"] == 2.0

    # It counts toward the limit they set for their own keys.
    client.put("/v1/ai/limit", json={"limit": 0.2}, headers=headers)
    with pytest.raises(SpendLimitReached) as reached:
        _as_user(client, user_id, routing.voyage)
    assert reached.value.own
    client.put("/v1/ai/limit", json={"limit": None}, headers=headers)

    # It has no models and is never the provider in use.
    assert (
        client.put("/v1/ai/provider", json={"provider": "voyage"}, headers=headers).status_code
        == 404
    )
    picks = {"analysis": "voyage-3-large", "synthesis": "voyage-3-large", "ask": "voyage-3-large"}
    assert client.put("/v1/ai/models/voyage", json=picks, headers=headers).status_code == 404

    # Without it they are back on the server's Voyage, already past their month of it.
    body = client.delete("/v1/ai/keys/voyage", headers=headers).json()
    assert body["provider"] == "anthropic"
    assert body["voyage"]["key"] is None and body["voyage"]["limit"] == 1.0
    with pytest.raises(SpendLimitReached) as capped:
        _as_user(client, user_id, routing.voyage)
    assert capped.value.voyage


def test_a_voyage_key_works_on_the_free_allowance_too_and_its_failures_are_the_users(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    own_voyage: type[_OwnVoyage],
) -> None:
    headers, user_id = account
    # A key Voyage turns down isn't saved.
    own_voyage.failure = voyage_error.AuthenticationError("Provided API key is invalid.", None, 401)
    refused = client.put("/v1/ai/keys/voyage", json={"key": VOYAGE_KEY}, headers=headers)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "Voyage didn't accept that key."
    assert client.get("/v1/ai", headers=headers).json()["voyage"]["key"] is None

    own_voyage.failure = None
    body = client.put("/v1/ai/keys/voyage", json={"key": VOYAGE_KEY}, headers=headers).json()
    # Still on the server's models, with the allowance used up; Voyage is theirs.
    assert body["provider"] is None and body["voyage"]["key"]["hint"] == "VOYG"
    _spend(db, user_id, "3")
    with pytest.raises(SpendLimitReached):
        _as_user(client, user_id, routing.resolve)
    assert _as_user(client, user_id, routing.voyage).own

    # A failure on it names Voyage and leaves the server's circuit alone.
    before = clients._breakers["voyage"].failures
    own_voyage.failure = voyage_error.AuthenticationError("Provided API key is invalid.", None, 401)
    with pytest.raises(ProviderError) as failed:
        _as_user(client, user_id, lambda: clients.voyage_embed(["a"], input_type="query"))
    assert user_message(failed.value) == "Voyage didn't accept your API key. Check it in Settings."
    assert not is_transient(failed.value)
    own_voyage.failure = voyage_error.APIConnectionError("no route to host")
    with pytest.raises(ProviderError) as unreachable:
        _as_user(client, user_id, lambda: clients.voyage_embed(["a"], input_type="query"))
    assert is_transient(unreachable.value)
    assert clients._breakers["voyage"].failures == before


def test_a_key_sealed_with_another_secret_reads_as_a_rejected_key(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    client.put("/v1/ai/keys/anthropic", json={"key": KEY}, headers=headers)
    monkeypatch.setattr(get_settings(), "keys_secret", "rotated-secret-" + "z" * 32)
    with pytest.raises(ProviderError) as failed:
        _as_user(client, user_id, routing.resolve)
    assert (
        user_message(failed.value) == "Anthropic didn't accept your API key. Check it in Settings."
    )
    # Theirs to fix, so a 4xx with the sentence, not a failure of the server's.
    refused = client.post("/v1/ask", json={"question": "anything?"}, headers=headers)
    assert refused.status_code == 424
    assert refused.json()["detail"] == user_message(failed.value)


# --- the calls ------------------------------------------------------------


class _Adapter:
    """Stands in for an adapter module: records what it was asked."""

    def __init__(self, usage: Usage) -> None:
        self.usage = usage
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def create(
        self, provider: str, key: str, params: dict[str, Any], *, timeout_s: float
    ) -> Message:
        self.sent.append((provider, key, params))
        return Message([Text("hello")], "end_turn", self.usage, model="gpt-6.1-sol-2026-10-01")


def _usage(db: Any, user_id: uuid.UUID) -> list[Any]:
    with db.connect() as conn:
        return list(
            conn.execute(
                select(LlmUsage.model, LlmUsage.billed_to, LlmUsage.cost_usd, LlmUsage.calls)
                .where(LlmUsage.user_id == user_id)
                .order_by(LlmUsage.model)
            )
        )


def test_a_call_on_an_own_key_goes_to_that_providers_model_and_is_billed_to_the_user(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    client.put("/v1/ai/keys/openai", json={"key": KEY}, headers=headers)
    adapter = _Adapter(Usage(input_tokens=1_000_000, output_tokens=100_000))
    monkeypatch.setitem(clients._ADAPTERS, "openai", adapter)

    async def call() -> Any:
        return await clients.claude(
            purpose="t",
            job="synthesis",
            effort="medium",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )

    message = _as_user(client, user_id, call)
    assert message.content[0].text == "hello"
    (provider, key, params) = adapter.sent[0]
    assert (provider, key) == ("openai", KEY)
    assert params["model"] == "gpt-6.1-sol"
    assert params["output_config"] == {"effort": "medium"}
    # Anthropic's refusal fallback is for Anthropic's API alone.
    assert "fallbacks" not in params and "betas" not in params
    # $2 in and $1 out, on the user's key, under the model that was asked for.
    assert _usage(db, user_id) == [("gpt-6.1-sol", "own", Decimal("3.00000000"), 1)]
    assert client.get("/v1/ai", headers=headers).json()["own"]["spent"] == 3.0


JOBS: tuple[catalog.Role, ...] = ("analysis", "synthesis", "ask")


def test_each_job_gets_the_users_model_for_it_whatever_the_server_runs_them_on(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    client.put("/v1/ai/keys/openai", json={"key": KEY}, headers=headers)
    picks = {"analysis": "gpt-6-luna", "synthesis": "gpt-6.1-sol", "ask": "gpt-6-astra"}
    client.put("/v1/ai/models/openai", json=picks, headers=headers)
    adapter = _Adapter(Usage())
    monkeypatch.setitem(clients._ADAPTERS, "openai", adapter)
    # A server that runs every job on one model says nothing about the user's.
    for job in JOBS:
        monkeypatch.setattr(get_settings(), f"claude_{job}_model", "claude-haiku-5-5")

    async def calls() -> None:
        for job in JOBS:
            await clients.claude(purpose="t", job=job, max_tokens=10)

    _as_user(client, user_id, calls)
    assert [params["model"] for _, _, params in adapter.sent] == list(picks.values())


async def test_on_the_servers_key_a_job_runs_on_the_model_set_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structlog.contextvars.clear_contextvars()
    asked: list[str] = []

    class Claude:
        def __init__(self) -> None:
            self.beta = SimpleNamespace(messages=self)

        async def create(self, **params: Any) -> SimpleNamespace:
            asked.append(params["model"])
            return SimpleNamespace(
                model=params["model"],
                stop_reason="end_turn",
                content=[],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    monkeypatch.setattr(clients, "anthropic_client", Claude)
    for job in JOBS:
        monkeypatch.setattr(get_settings(), f"claude_{job}_model", f"claude-for-{job}")
    for job in JOBS:
        await clients.claude(purpose="t", job=job, max_tokens=10)
    assert asked == ["claude-for-analysis", "claude-for-synthesis", "claude-for-ask"]


def test_a_failure_on_an_own_key_is_the_users_to_fix_and_leaves_the_servers_circuit_alone(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    client.put("/v1/ai/keys/gemini", json={"key": KEY}, headers=headers)

    class Failing:
        async def create(self, provider: str, *_: Any, **__: Any) -> Message:
            raise ProviderError(provider, 429, "quota", 3.0)

    monkeypatch.setitem(clients._ADAPTERS, "gemini", Failing())
    breaker = clients._breakers["anthropic"]
    before = breaker.failures

    async def call() -> Any:
        return await clients.claude(purpose="t", job="analysis", max_tokens=10)

    for _ in range(clients._Breaker.THRESHOLD + 1):
        with pytest.raises(ProviderError) as failed:
            _as_user(client, user_id, call)
    assert breaker.failures == before
    assert is_transient(failed.value)
    assert user_message(failed.value) == (
        "Google Gemini says your key is out of credit or over its rate limit."
    )


def test_a_call_on_the_servers_key_is_priced_and_counts_toward_the_allowance(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    answer = SimpleNamespace(
        model="claude-opus-5-5",
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="hi")],
        usage=SimpleNamespace(input_tokens=500_000, output_tokens=50_000),
    )

    class Claude:
        def __init__(self) -> None:
            self.beta = SimpleNamespace(messages=self)

        async def create(self, **params: Any) -> SimpleNamespace:
            return answer

    class Voyage:
        async def rerank(self, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(results=[], total_tokens=1_000_000)

    monkeypatch.setattr(clients, "anthropic_client", Claude)
    monkeypatch.setattr(clients, "voyage_client", Voyage)

    async def call() -> None:
        await clients.voyage_rerank("q", ["d"], 1)
        await clients.claude(purpose="t", job="ask", model="claude-opus-5-5", max_tokens=10)

    _as_user(client, user_id, call)
    # $0.05 of reranking, then $2 in and $1 out on Opus: over the $3 together.
    assert _usage(db, user_id) == [
        ("claude-opus-5-5", "platform", Decimal("3.00000000"), 1),
        ("rerank-2.5", "platform", Decimal("0.05000000"), 1),
    ]
    assert client.get("/v1/ai", headers=headers).json()["free"]["spent"] == 3.05
    with pytest.raises(SpendLimitReached):
        _as_user(client, user_id, call)
    assert len(_usage(db, user_id)) == 2


# --- the routes that spend ------------------------------------------------


def test_past_the_limit_ask_and_retry_say_so_before_they_start(
    client: TestClient, db: Any, account: tuple[dict[str, str], uuid.UUID], jobs: Any
) -> None:
    headers, user_id = account
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url="https://example.com/a",
                source="web",
                status="failed",
            )
        )
    _spend(db, user_id, "3")
    for resp in (
        client.post("/v1/ask", json={"question": "anything?"}, headers=headers),
        client.post("/v1/ask/stream", json={"question": "anything?"}, headers=headers),
        client.post(f"/v1/items/{item_id}/retry", headers=headers),
    ):
        assert resp.status_code == 402, resp.text
        assert resp.json()["detail"].startswith("You've used this month's free $3.00.")
    assert jobs == []
    # A save is still taken: it is kept, and fails with the reason later.
    saved = client.post("/v1/ingest", json={"url": "https://example.com/b"}, headers=headers)
    assert saved.status_code == 202


# --- what the review found -------------------------------------------------


def test_a_key_that_cant_travel_in_a_header_is_refused_before_any_provider_sees_it(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    accepted: list[tuple[str, str, str | None]],
) -> None:
    headers, _ = account
    for pasted in ("sk-\u2026abcdefgh1234", "sk-abcd\u200befgh1234", "sk-\u201cabcdefgh1234\u201d"):
        for provider in ("openai", "gemini", "anthropic"):
            resp = client.put(f"/v1/ai/keys/{provider}", json={"key": pasted}, headers=headers)
            assert resp.status_code == 422, resp.text
            assert resp.json()["detail"] == "That doesn't look like a key."
    assert accepted == []


class _Server:
    """Stands in for the server's Claude and Voyage clients."""

    def __init__(self, stream: Any = None) -> None:
        self.beta = SimpleNamespace(messages=self)
        self._stream = stream

    async def create(self, **params: Any) -> SimpleNamespace:
        return SimpleNamespace(
            model="claude-sonnet-5-5",
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="hi")],
            usage=SimpleNamespace(input_tokens=500_000, output_tokens=0),
        )

    @contextlib.asynccontextmanager
    async def stream(self, **params: Any) -> AsyncIterator[Any]:
        yield self._stream

    async def rerank(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(results=[], total_tokens=1_000_000)


def test_taking_a_deleted_item_out_of_the_wiki_is_never_held_back_or_billed_to_the_user(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers, user_id = account
    _spend(db, user_id, "3")
    monkeypatch.setattr(clients, "anthropic_client", _Server)
    monkeypatch.setattr(clients, "voyage_client", _Server)

    async def cleanup() -> None:
        await clients.voyage_rerank("q", ["d"], 1)
        await clients.claude(purpose="forget", job="synthesis", max_tokens=10)

    async def forget_item() -> None:
        structlog.contextvars.bind_contextvars(job="forget_item")
        await cleanup()

    with pytest.raises(SpendLimitReached):
        _as_user(client, user_id, cleanup)
    _as_user(client, user_id, forget_item)
    # The server's own work: its cost is kept, and counted against nobody.
    exempt = [row for row in _usage(db, user_id) if row.billed_to == "exempt"]
    assert [(row.model, row.cost_usd) for row in exempt] == [
        ("claude-sonnet-5-5", Decimal("1.00000000")),
        ("rerank-2.5", Decimal("0.05000000")),
    ]
    assert client.get("/v1/ai", headers=headers).json()["free"]["spent"] == 3.0


def test_an_answer_left_before_its_end_still_counts(
    client: TestClient,
    db: Any,
    account: tuple[dict[str, str], uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account

    class Stream:
        # What the SDK knows mid-answer: the prompt's count, and the text so far.
        current_message_snapshot = SimpleNamespace(
            model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=250_000, output_tokens=1),
            content=[
                SimpleNamespace(type="thinking", thinking="t" * 40_000),
                SimpleNamespace(type="text", text="x" * 160_000),
            ],
        )

        async def get_final_message(self) -> Any:
            raise AssertionError("the answer wasn't read to its end")

    monkeypatch.setattr(clients, "anthropic_client", lambda: _Server(Stream()))

    async def stopped() -> None:
        with pytest.raises(asyncio.CancelledError):
            async with clients.claude_stream(purpose="ask", job="ask", max_tokens=9):
                # The app's Stop: the request is cancelled mid-answer.
                raise asyncio.CancelledError
        await asyncio.gather(*clients._settling)

    _as_user(client, user_id, stopped)
    # $1 of prompt, and 50,000 tokens of answer ($1) told from its 200,000 characters.
    assert _usage(db, user_id) == [("claude-opus-5-5", "platform", Decimal("2.00000000"), 1)]

    # A stream nothing came from has spent nothing that is known.
    monkeypatch.setattr(clients, "anthropic_client", lambda: _Server(SimpleNamespace()))
    _as_user(client, user_id, stopped)
    assert len(_usage(db, user_id)) == 1


async def test_own_key_jobs_leave_a_slot_for_the_users_own_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits: list[int] = []

    @contextlib.asynccontextmanager
    async def slot(provider: str, limit: int) -> AsyncIterator[None]:
        limits.append(limit)
        yield

    monkeypatch.setattr(clients, "_slot", slot)
    route = routing.Route(provider="openai", own=True, key=KEY, user_id=uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    async with clients._calling_own(route):
        pass
    structlog.contextvars.bind_contextvars(job="ingest_item")
    try:
        async with clients._calling_own(route):
            pass
    finally:
        structlog.contextvars.clear_contextvars()
    assert limits == [clients.OWN_CONCURRENCY, clients.OWN_CONCURRENCY - 1]


def test_a_failed_save_records_the_sentence_for_the_user_never_the_providers_words() -> None:
    echoed = ProviderError("openai", 401, "Incorrect API key provided: sk-abcd1234***wxyz")
    assert _reason(echoed) == (
        "ProviderError: OpenAI didn't accept your API key. Check it in Settings."
    )
    limit = SpendLimitReached(own=False, limit=Decimal("3"), resets=date(2026, 12, 1))
    assert _reason(limit) == f"SpendLimitReached: {limit}"
    assert _reason(ValueError("no body")) == "ValueError: no body"


async def test_a_page_rewrite_past_the_limit_leaves_the_page_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def claude(**_: Any) -> Any:
        raise SpendLimitReached(own=False, limit=Decimal("3"), resets=date(2026, 12, 1))

    monkeypatch.setattr(synthesize_ai, "claude", claude)
    rewritten = await synthesize_ai.correct_page(
        slug="ramen", title="Ramen", body="Body.", corrections=["It closed in 2024."]
    )
    assert rewritten is None
