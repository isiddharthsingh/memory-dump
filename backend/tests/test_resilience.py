"""Retries, backoff, the provider breaker and slots, rate limits, caps, and
upload storage. Run via `docker compose exec api pytest tests/test_resilience.py`
(the slot and bucket tests use the stack's Redis)."""

import gzip
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import structlog
from fastapi.testclient import TestClient
from voyageai.error import InvalidRequestError, RateLimitError

from app import kv
from app.ai import clients, embed
from app.ai.clients import _Breaker
from app.api import search as search_api
from app.auth.rate_limit import take
from app.config import get_settings
from app.errors import ProviderUnavailable, is_transient
from app.extractors.webpage import NotPublic, TooLarge, _CappedClient
from app.main import app
from app.storage import LocalStorage
from app.workers.jobs import MAX_DELAY_S, last_try, later
from tests.conftest import form, signup, unique_email

REQUEST = httpx.Request("GET", "https://example.com/")


def _status(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "status", request=REQUEST, response=httpx.Response(code, request=REQUEST)
    )


def test_transient_failures_are_the_ones_worth_another_try() -> None:
    assert is_transient(ProviderUnavailable("anthropic", 5))
    assert is_transient(RateLimitError("slow down"))
    assert is_transient(httpx.ConnectTimeout("slow"))
    assert is_transient(TimeoutError())
    assert is_transient(_status(503))
    assert is_transient(_status(429))
    assert not is_transient(_status(404))
    assert not is_transient(InvalidRequestError("bad input"))
    assert not is_transient(NotPublic("10.0.0.1"))
    assert not is_transient(httpx.UnsupportedProtocol("intent://open"))


def test_backoff_doubles_with_jitter_and_honors_retry_after() -> None:
    for job_try in (1, 2, 3):
        ceiling = 60 * 2 ** (job_try - 1)
        delay = later({"job_try": job_try}, 60).defer_score / 1000  # type: ignore[operator]
        assert ceiling / 2 <= delay <= ceiling
    assert later({"job_try": 30}, 60).defer_score <= MAX_DELAY_S * 1000  # type: ignore[operator]
    asked = later({"job_try": 1}, 60, ProviderUnavailable("voyage", 300))
    assert asked.defer_score >= 300_000  # type: ignore[operator]


def test_site_failures_get_fewer_tries_than_provider_ones() -> None:
    assert last_try({"job_try": 3}, httpx.ConnectError("down"))
    assert not last_try({"job_try": 3}, RateLimitError("slow down"))
    assert last_try({"job_try": 8}, RateLimitError("slow down"))


def test_breaker_opens_after_transient_failures_in_a_row() -> None:
    breaker = _Breaker("voyage")
    for _ in range(_Breaker.THRESHOLD):
        breaker.check()
        breaker.failed(RateLimitError("slow down"))
    with pytest.raises(ProviderUnavailable):
        breaker.check()
    breaker.succeeded()
    breaker.check()

    # A request that is simply wrong doesn't open it.
    other = _Breaker("anthropic")
    for _ in range(_Breaker.THRESHOLD * 2):
        other.failed(InvalidRequestError("bad input"))
    other.check()


async def test_the_slot_cap_holds_extra_calls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients, "SLOT_WAIT_S", 0.3)
    provider = f"test-{uuid.uuid4().hex[:8]}"
    async with clients._slot(provider, 1):
        with pytest.raises(ProviderUnavailable):
            async with clients._slot(provider, 1):
                pass
    # Freed once the first call ended.
    async with clients._slot(provider, 1):
        pass


def test_jobs_leave_a_quarter_of_the_slots_to_requests() -> None:
    structlog.contextvars.clear_contextvars()
    assert clients._cap(16) == 16
    structlog.contextvars.bind_contextvars(job="synthesize_topics")
    try:
        assert clients._cap(16) == 12
        assert clients._cap(1) == 1
        assert clients._cap(0) == 0
    finally:
        structlog.contextvars.clear_contextvars()


async def test_embeddings_go_to_voyage_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    structlog.contextvars.clear_contextvars()
    sizes: list[int] = []

    class Voyage:
        async def embed(self, texts: list[str], **_: Any) -> SimpleNamespace:
            sizes.append(len(texts))
            return SimpleNamespace(embeddings=[[0.0]] * len(texts), total_tokens=len(texts))

    monkeypatch.setattr(clients, "voyage_client", Voyage)
    out = await clients.voyage_embed(["text"] * 300, input_type="document")
    assert sizes == [128, 128, 44]
    assert len(out) == 300


async def test_query_embeddings_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def embed_texts(texts: list[str], input_type: str) -> list[list[float]]:
        calls.append(texts)
        return [[0.25, 0.5]]

    monkeypatch.setattr(embed, "embed_texts", embed_texts)
    query = f"cache test {uuid.uuid4()}"
    assert await embed.embed_query(query) == [0.25, 0.5]
    assert await embed.embed_query(query) == [0.25, 0.5]
    assert len(calls) == 1


async def test_page_bodies_are_capped_after_decompression() -> None:
    body = gzip.compress(b"a" * 5000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"})

    transport = httpx.MockTransport(handler)
    async with _CappedClient(transport=transport, max_bytes=1000) as client:
        with pytest.raises(TooLarge):
            await client.get("https://example.com/")
    async with _CappedClient(transport=transport, max_bytes=10_000) as client:
        assert (await client.get("https://example.com/")).text == "a" * 5000


async def test_redirect_bodies_are_never_read() -> None:
    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            raise AssertionError("the redirect's body was read")
            yield b""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/from":
            return httpx.Response(302, headers={"Location": "/to"}, stream=Endless())
        return httpx.Response(200, content=b"landed")

    transport = httpx.MockTransport(handler)
    async with _CappedClient(transport=transport, max_bytes=100, follow_redirects=True) as client:
        resp = await client.get("https://example.com/from")
    assert resp.text == "landed"
    assert str(resp.url) == "https://example.com/to"


async def test_local_storage_round_trip_and_sweep(tmp_path: Any) -> None:
    store = LocalStorage(str(tmp_path))

    async def chunks() -> AsyncIterator[bytes]:
        yield b"ab"
        yield b"cd"

    await store.save("x.pdf", chunks())
    assert await store.read("x.pdf") == b"abcd"
    abandoned = tmp_path / "old.pdf"
    abandoned.write_bytes(b"1")
    os.utime(abandoned, (0, 0))
    assert await store.sweep() == 1
    assert not abandoned.exists()
    assert (tmp_path / "x.pdf").exists()
    with pytest.raises(ValueError):
        await store.read("../etc/passwd")
    await store.delete("x.pdf")
    await store.delete("x.pdf")


async def test_a_bucket_allows_its_allowance_then_says_how_long_to_wait() -> None:
    key = f"rl:test:{uuid.uuid4()}"
    assert [await take(key, 3) for _ in range(3)] == [0, 0, 0]
    wait = await take(key, 3)
    # One more request every 3600/3 seconds.
    assert 0 < wait <= 1200
    assert await take(key, 0) == 0


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _clear(client: TestClient, key: str) -> None:
    async def delete() -> None:
        await kv.redis().delete(key)

    client.portal.call(delete)  # type: ignore[union-attr]


def test_sign_in_attempts_are_capped_per_address(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "rl:auth:testclient"
    _clear(client, key)
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_hour", 2)
    try:
        attempt = form(unique_email("limit"), password="wrong-password-123")
        headers = {"st-auth-mode": "header"}
        for _ in range(2):
            assert client.post("/auth/signin", json=attempt, headers=headers).status_code == 200
        refused = client.post("/auth/signin", json=attempt, headers=headers)
        assert refused.status_code == 429
        assert int(refused.headers["retry-after"]) > 0
        assert refused.json() == {"detail": "Too many attempts. Try again later."}
    finally:
        _clear(client, key)


def test_search_is_rate_limited_per_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hybrid_search(*_: Any, **__: Any) -> list[Any]:
        return []

    monkeypatch.setattr(search_api, "hybrid_search", hybrid_search)
    auth = signup(client)
    try:
        too_long = client.post("/v1/search", json={"q": "x" * 1001}, headers=auth)
        assert too_long.status_code == 422
        monkeypatch.setattr(get_settings(), "rate_limit_search_per_hour", 1)
        assert client.post("/v1/search", json={"q": "anything"}, headers=auth).status_code == 200
        limited = client.post("/v1/search", json={"q": "anything"}, headers=auth)
        assert limited.status_code == 429
        assert "retry-after" in limited.headers
    finally:
        client.delete("/auth/me", headers=auth)
