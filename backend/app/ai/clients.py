"""One Claude client and one Voyage client per process, and the only way calls
reach them: `claude`, `claude_stream`, `voyage_embed`, `voyage_rerank`.

Each call
- fails fast with `ProviderUnavailable` while the provider's circuit is open,
  after `_Breaker.THRESHOLD` transient failures in a row in this process;
- holds a slot of the provider's cap on calls in flight, shared through Redis
  by every API and worker process (ANTHROPIC_MAX_CONCURRENCY,
  VOYAGE_MAX_CONCURRENCY), so a burst of jobs and questions stays inside the
  account's rate limits; jobs get at most JOB_SHARE of the slots, so a long
  queue never leaves a question waiting;
- records the tokens it spent (app/usage.py).

Claude calls go through the beta Messages endpoint so models that have one get
Anthropic's server-side fallback on a refusal (`fallbacks: "default"`); a
refusal that stands raises `Refused`. `effort` sets how much the model thinks
(the 5.5 models think by default, and thinking counts toward `max_tokens`), and
`claude_json` asks for output that matches a JSON schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal, cast

import anthropic
import structlog
import voyageai
from anthropic.lib.streaming import BetaAsyncMessageStream
from anthropic.types.beta import BetaMessage
from redis.exceptions import RedisError

from app import kv, usage
from app.config import get_settings
from app.errors import ProviderUnavailable, Refused, is_transient
from app.metrics import PROVIDER_CALLS

log = logging.getLogger(__name__)

# Voyage takes up to 1000 texts and 120k tokens a request; chunks are about
# 500 tokens (app/ai/embed.py), so 128 of them stay well inside both.
EMBED_BATCH = 128

_clients: dict[str, tuple[asyncio.AbstractEventLoop, Any]] = {}


def _per_loop[T](name: str, make: Callable[[], T]) -> T:
    """One client per event loop, like app.kv: their connection pools belong
    to the loop that opened them."""
    loop = asyncio.get_running_loop()
    cached = _clients.get(name)
    if cached is None or cached[0] is not loop:
        cached = (loop, make())
        _clients[name] = cached
    return cast(T, cached[1])


def anthropic_client() -> anthropic.AsyncAnthropic:
    s = get_settings()
    return _per_loop(
        "anthropic",
        lambda: anthropic.AsyncAnthropic(
            api_key=s.anthropic_api_key, timeout=s.anthropic_timeout_s, max_retries=2
        ),
    )


def voyage_client() -> voyageai.AsyncClient:
    s = get_settings()
    return _per_loop(
        "voyage",
        lambda: voyageai.AsyncClient(
            api_key=s.voyage_api_key, max_retries=2, timeout=s.voyage_timeout_s
        ),
    )


class _Breaker:
    """Stops calling a provider for COOLDOWN_S after THRESHOLD transient
    failures in a row. After the cooldown calls go through again; the next
    failure opens it at once, the next success closes it."""

    THRESHOLD = 5
    COOLDOWN_S = 30.0

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.failures = 0
        self.open_until = 0.0

    def check(self) -> None:
        wait = self.open_until - time.monotonic()
        if wait > 0:
            raise ProviderUnavailable(self.provider, wait)

    def succeeded(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def failed(self, exc: BaseException) -> None:
        if not is_transient(exc) or isinstance(exc, ProviderUnavailable):
            return
        self.failures += 1
        if self.failures >= self.THRESHOLD:
            self.open_until = time.monotonic() + self.COOLDOWN_S
            log.warning(
                "%s failed %d times in a row; not calling it for %ds",
                self.provider,
                self.failures,
                self.COOLDOWN_S,
            )


_breakers = {"anthropic": _Breaker("anthropic"), "voyage": _Breaker("voyage")}

# Takes a slot when fewer than ARGV[1] are held. A slot is a member of a
# sorted set scored by when its lease ends, so one whose holder died frees
# itself.
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[1]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""
# Longer than any call takes.
_LEASE_MS = 15 * 60 * 1000
# How long a call waits for a slot before giving up as unavailable.
SLOT_WAIT_S = 30.0
# Jobs may hold this share of a provider's slots at most; the rest stay free
# for requests someone is waiting on (Ask, Search), however long the queue.
JOB_SHARE = 0.75


def _cap(limit: int) -> int:
    """The slots this call may count up to: all of them for a request, the
    jobs' share inside a job (app/workers/jobs.py binds `job`)."""
    if limit <= 0 or "job" not in structlog.contextvars.get_contextvars():
        return limit
    return max(1, int(limit * JOB_SHARE))


@asynccontextmanager
async def _slot(provider: str, limit: int) -> AsyncIterator[None]:
    if limit <= 0:
        yield
        return
    key = f"slots:{provider}"
    holder = uuid.uuid4().hex
    held = False
    deadline = time.monotonic() + SLOT_WAIT_S
    try:
        while True:
            try:
                acquire = kv.redis().register_script(_ACQUIRE)
                held = bool(await acquire(keys=[key], args=[limit, _LEASE_MS, holder]))
            except RedisError:
                log.warning("provider slots unavailable, calling %s without one", provider)
                break
            if held:
                break
            if time.monotonic() > deadline:
                raise ProviderUnavailable(provider, retry_after=SLOT_WAIT_S)
            await asyncio.sleep(random.uniform(0.05, 0.25))
        yield
    finally:
        if held:
            try:
                await kv.redis().zrem(key, holder)
            except RedisError:
                log.warning("couldn't free a %s slot; its lease will", provider)


@asynccontextmanager
async def _calling(provider: str) -> AsyncIterator[None]:
    breaker = _breakers[provider]
    breaker.check()
    s = get_settings()
    limit = s.anthropic_max_concurrency if provider == "anthropic" else s.voyage_max_concurrency
    async with _slot(provider, _cap(limit)):
        try:
            yield
        except Exception as exc:
            breaker.failed(exc)
            PROVIDER_CALLS.labels(provider, "transient" if is_transient(exc) else "error").inc()
            raise
        breaker.succeeded()
        PROVIDER_CALLS.labels(provider, "ok").inc()


Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Models whose refusals the API retries on the model Anthropic picks for the
# refusal's category. Haiku 5.5 has no server-side fallback (a list of models
# 400s), and older models don't take the "default" form.
_FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5", "claude-fable-5-1"}
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _request(params: dict[str, Any], effort: Effort | None) -> dict[str, Any]:
    config = dict(params.pop("output_config", None) or {})
    if effort is not None:
        config["effort"] = effort
    if config:
        params["output_config"] = config
    if params["model"] in _FALLBACK_MODELS:
        params["betas"] = [*params.get("betas", ()), _FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


async def _settle(purpose: str, message: BetaMessage) -> None:
    """Records the tokens, then raises `Refused` when the answer is a refusal."""
    await usage.record_claude(purpose, message.model, message.usage)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise Refused(getattr(details, "category", None))


async def claude(
    *,
    purpose: str,
    timeout_s: float | None = None,
    effort: Effort | None = None,
    **params: Any,
) -> BetaMessage:
    """One Messages API call. `purpose` labels its tokens (analyze, ask, ...);
    `timeout_s` replaces ANTHROPIC_TIMEOUT_S for it."""
    params = _request(params, effort)
    async with _calling("anthropic"):
        client = anthropic_client()
        if timeout_s is not None:
            client = client.with_options(timeout=timeout_s)
        message = cast(BetaMessage, await client.beta.messages.create(**params))
    await _settle(purpose, message)
    return message


async def claude_json(
    *,
    purpose: str,
    schema: dict[str, Any],
    timeout_s: float | None = None,
    effort: Effort | None = None,
    **params: Any,
) -> dict[str, Any]:
    """A call whose answer is JSON matching `schema` (structured outputs), parsed.
    Raises ValueError when the answer was cut off at `max_tokens`. Streamed, so
    a long answer (a PDF's whole text) isn't cut off by the HTTP timeout."""
    params["output_config"] = {
        **(params.get("output_config") or {}),
        "format": {"type": "json_schema", "schema": schema},
    }
    async with claude_stream(
        purpose=purpose, timeout_s=timeout_s, effort=effort, **params
    ) as stream:
        message = await stream.get_final_message()
    if message.stop_reason == "max_tokens":
        raise ValueError(f"{purpose}: the answer was cut off at max_tokens")
    text = "".join(b.text for b in message.content if b.type == "text")
    return cast(dict[str, Any], json.loads(text))


@asynccontextmanager
async def claude_stream(
    *,
    purpose: str,
    timeout_s: float | None = None,
    effort: Effort | None = None,
    **params: Any,
) -> AsyncIterator[BetaAsyncMessageStream]:
    """A streamed Messages API call, holding its slot until the stream ends.
    A refusal raises `Refused` when the stream closes."""
    params = _request(params, effort)
    async with _calling("anthropic"):
        client = anthropic_client()
        if timeout_s is not None:
            client = client.with_options(timeout=timeout_s)
        async with client.beta.messages.stream(**params) as stream:
            yield stream
            message = await stream.get_final_message()
    await _settle(purpose, message)


async def voyage_embed(texts: list[str], *, input_type: str) -> list[list[float]]:
    model = get_settings().voyage_embed_model
    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        async with _calling("voyage"):
            result = await voyage_client().embed(
                texts=texts[start : start + EMBED_BATCH],
                model=model,
                input_type=input_type,
                truncation=True,
            )
        out.extend(cast(list[list[float]], result.embeddings))
        await usage.record_voyage(f"embed_{input_type}", model, result.total_tokens)
    return out


async def voyage_rerank(query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
    model = get_settings().voyage_rerank_model
    async with _calling("voyage"):
        result = await voyage_client().rerank(
            query=query, documents=documents, model=model, top_k=top_k
        )
    await usage.record_voyage("rerank", model, result.total_tokens)
    return [(r.index, r.relevance_score) for r in result.results]
