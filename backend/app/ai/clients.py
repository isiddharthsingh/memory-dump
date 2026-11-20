"""One Claude client and one Voyage client per process, and the only way calls
reach them: `claude`, `claude_stream`, `voyage_embed`, `voyage_rerank`.

A call is first given its route (app/ai/routing.py): the server's key, while
the user's free allowance for the month lasts, or the key the user saved for
a provider of their own. On their own key the same request goes to that
provider's model for the job: through the Anthropic SDK for Anthropic and
Amazon Bedrock, through an adapter that speaks the provider's API for OpenAI,
OpenRouter and Gemini (app/ai/wire.py). The answer reads the same either way.

Each call on the server's keys
- fails fast with `ProviderUnavailable` while the provider's circuit is open,
  after `_Breaker.THRESHOLD` transient failures in a row in this process;
- holds a slot of the provider's cap on calls in flight, shared through Redis
  by every API and worker process (ANTHROPIC_MAX_CONCURRENCY,
  VOYAGE_MAX_CONCURRENCY), so a burst of jobs and questions stays inside the
  account's rate limits; jobs get at most JOB_SHARE of the slots, so a long
  queue never leaves a question waiting;
- records the tokens it spent and what they cost (app/usage.py).

A call on a user's own key takes none of those slots and doesn't move the
circuit, which guard the server's accounts; a user has OWN_CONCURRENCY of
their own, and a failure there is a `ProviderError`. Voyage calls go the same
two ways: on the server's key, or on the Voyage key the user saved.

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
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from typing import Any, Literal, cast

import anthropic
import structlog
import voyageai
from anthropic.lib.streaming import BetaAsyncMessageStream
from anthropic.types.beta import BetaMessage
from redis.exceptions import RedisError
from voyageai import error as voyage_error

from app import kv, usage
from app.ai import catalog, gemini, openai_chat, routing, wire
from app.ai.breaker import Breaker as _Breaker
from app.ai.routing import Route
from app.ai.wire import Message
from app.config import get_settings
from app.errors import ProviderUnavailable, Refused, is_transient, retry_after
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


def _voyage(route: Route, timeout_s: float | None = None) -> voyageai.AsyncClient:
    """The Voyage client a route's call is made with: the server's, or one
    made for this call that knows only the user's key."""
    if not route.own:
        return voyage_client()
    if not route.key:
        # The SDK would fall back to a key from the environment: the server's.
        raise ValueError("a call on a user's own key needs the key")
    timeout = timeout_s or get_settings().voyage_timeout_s
    return voyageai.AsyncClient(api_key=route.key, max_retries=2, timeout=timeout)


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
# Calls one user may have in flight on their own key, so a burst of saves
# stays inside a new account's rate limits.
OWN_CONCURRENCY = 4
# The providers reached through an adapter instead of the Anthropic SDK.
_ADAPTERS: dict[str, Any] = {"openai": openai_chat, "openrouter": openai_chat, "gemini": gemini}
# How long checking a key a user is saving may take.
KEY_CHECK_TIMEOUT_S = 20.0


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


@contextmanager
def _as_provider_errors(provider: str) -> Iterator[None]:
    """The Anthropic and Voyage SDKs' failures on a user's key as the
    adapters raise them."""
    try:
        yield
    except anthropic.APIStatusError as exc:
        raise wire.ProviderError(provider, exc.status_code, exc.message, retry_after(exc)) from exc
    except anthropic.APIConnectionError as exc:
        raise wire.ProviderError(provider, None, repr(exc)) from exc
    except voyage_error.VoyageError as exc:
        # No status when it never answered: no connection, a timeout.
        raise wire.ProviderError(provider, exc.http_status, str(exc)) from exc


@asynccontextmanager
async def _calling_own(route: Route) -> AsyncIterator[None]:
    label = f"{route.provider}-own"
    # Like the server's slots, a share stays free for the user's requests:
    # their own saves in flight never keep their question waiting.
    async with _slot(f"own:{route.user_id}", _cap(OWN_CONCURRENCY)):
        try:
            with _as_provider_errors(route.provider):
                yield
        except Exception as exc:
            PROVIDER_CALLS.labels(label, "transient" if is_transient(exc) else "error").inc()
            raise
        PROVIDER_CALLS.labels(label, "ok").inc()


def _call(route: Route) -> AbstractAsyncContextManager[None]:
    return _calling_own(route) if route.own else _calling(route.provider)


def _sdk(route: Route, timeout_s: float | None) -> Any:
    """The Anthropic SDK client a route's call is made with: the server's, or
    one made for this call that knows only the user's key. Those share one
    pool of connections, so a call on a user's key opens none of its own."""
    if not route.own:
        client = anthropic_client()
        return client if timeout_s is None else client.with_options(timeout=timeout_s)
    if not route.key:
        # The SDK would fall back to a key from the environment: the server's.
        raise ValueError("a call on a user's own key needs the key")
    pool = _per_loop("own-pool", anthropic.DefaultAsyncHttpxClient)
    if route.provider == "bedrock":
        # Bedrock's Messages endpoint in the key's region: the same API, the
        # Bedrock key as its bearer token.
        return anthropic.AsyncAnthropicBedrockMantle(
            api_key=route.key,
            aws_region=route.region or catalog.DEFAULT_REGION,
            timeout=_timeout(timeout_s),
            max_retries=2,
            http_client=pool,
        )
    return anthropic.AsyncAnthropic(
        api_key=route.key, timeout=_timeout(timeout_s), max_retries=2, http_client=pool
    )


def _timeout(timeout_s: float | None) -> float:
    return get_settings().anthropic_timeout_s if timeout_s is None else timeout_s


async def check_key(provider: str, key: str, region: str | None = None) -> None:
    """Asks the provider for something only a working key gets. Raises
    `ProviderError` when it doesn't, or when the provider can't be asked."""
    if provider in _ADAPTERS:
        await _ADAPTERS[provider].check_key(provider, key)
        return
    if provider == catalog.VOYAGE.id:
        # Voyage lists no models either; embedding a word costs next to nothing.
        voyage = _voyage(Route(provider=provider, own=True, key=key), KEY_CHECK_TIMEOUT_S)
        with _as_provider_errors(provider):
            await voyage.embed(["key"], model=get_settings().voyage_embed_model, input_type="query")
        return
    client = _sdk(Route(provider=provider, own=True, key=key, region=region), KEY_CHECK_TIMEOUT_S)
    with _as_provider_errors(provider):
        if provider == "bedrock":
            # Bedrock lists no models; counting a prompt's tokens costs
            # nothing and needs the key and the model both.
            await client.messages.count_tokens(
                model=catalog.PROVIDERS[provider].defaults["analysis"],
                messages=[{"role": "user", "content": "hi"}],
            )
        else:
            await client.models.list(limit=1)


Effort = Literal["low", "medium", "high", "xhigh", "max"]
# What a call is for, which picks its model: reading a save, writing the
# wiki, answering.
Job = catalog.Role

# Models whose refusals the API retries on the model Anthropic picks for the
# refusal's category. Haiku 5.5 has no server-side fallback (a list of models
# 400s), and older models don't take the "default" form.
_FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5", "claude-fable-5-1"}
)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _request(
    params: dict[str, Any], job: Job, effort: Effort | None, route: Route
) -> dict[str, Any]:
    """The request as it is sent: on the model that does `job` for whoever's
    key the route is, the user's pick on their own key and the server's
    (CLAUDE_ANALYSIS_MODEL, …) on its own. A `model` already in `params`
    names another of the server's, for a test."""
    config = dict(params.pop("output_config", None) or {})
    if effort is not None:
        config["effort"] = effort
    if config:
        params["output_config"] = config
    if route.own and route.models:
        params["model"] = route.models[job]
    else:
        params.setdefault("model", getattr(get_settings(), f"claude_{job}_model"))
    # Anthropic's own API only: Bedrock and the adapters take neither field.
    if route.provider == "anthropic" and params["model"] in _FALLBACK_MODELS:
        params["betas"] = [*params.get("betas", ()), _FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


async def _settle(purpose: str, route: Route, asked: str, message: BetaMessage | Message) -> None:
    """Records the tokens, then raises `Refused` when the answer is a refusal.
    An own key's tokens go under the model that was asked for, the id its
    price is listed by; providers answer with a dated name of their own."""
    model = asked if route.own else message.model
    await usage.record_claude(purpose, model, message.usage, provider=route.provider, own=route.own)
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        claude = route.provider in ("anthropic", "bedrock") or "claude" in model
        raise Refused(getattr(details, "category", None), by="Claude" if claude else "The model")


async def claude(
    *,
    purpose: str,
    job: Job,
    timeout_s: float | None = None,
    effort: Effort | None = None,
    **params: Any,
) -> BetaMessage | Message:
    """One Messages API call. `purpose` labels its tokens (analyze, ask, ...);
    `job` picks its model; `timeout_s` replaces ANTHROPIC_TIMEOUT_S for it."""
    route = await routing.resolve()
    params = _request(params, job, effort, route)
    message: BetaMessage | Message
    async with _call(route):
        if route.provider in _ADAPTERS:
            message = await _ADAPTERS[route.provider].create(
                route.provider, route.key, params, timeout_s=_timeout(timeout_s)
            )
        else:
            client = _sdk(route, timeout_s)
            message = cast(BetaMessage, await client.beta.messages.create(**params))
    await _settle(purpose, route, params["model"], message)
    return message


async def claude_json(
    *,
    purpose: str,
    job: Job,
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
        purpose=purpose, job=job, timeout_s=timeout_s, effort=effort, **params
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
    job: Job,
    timeout_s: float | None = None,
    effort: Effort | None = None,
    **params: Any,
) -> AsyncIterator[BetaAsyncMessageStream | wire.Stream]:
    """A streamed Messages API call, holding its slot until the stream ends.
    A refusal raises `Refused` when the stream closes."""
    route = await routing.resolve()
    params = _request(params, job, effort, route)
    message: BetaMessage | Message
    async with _call(route):
        if route.provider in _ADAPTERS:
            async with _ADAPTERS[route.provider].stream(
                route.provider, route.key, params, timeout_s=_timeout(timeout_s)
            ) as answer:
                yield answer
                message = await answer.get_final_message()
        else:
            async with _sdk(route, timeout_s).beta.messages.stream(**params) as stream:
                try:
                    yield stream
                    message = await stream.get_final_message()
                except BaseException:
                    _abandoned(purpose, route, params["model"], stream)
                    raise
    await _settle(purpose, route, params["model"], message)


# The records of abandoned streams still being written, held so that none is
# collected before it is.
_settling: set[asyncio.Task[None]] = set()


def _abandoned(purpose: str, route: Route, asked: str, stream: Any) -> None:
    """Records what a stream had spent when it was left before its end: the
    app's Stop, a dropped connection, a failure halfway. Otherwise an answer
    cancelled before its last word would be free against the month's
    allowance. The prompt's tokens are known from the first event; the
    answer's are only counted in the last, so they are estimated from the
    text so far, at four characters a token. Written by a task of its own,
    because the one that leaves the stream is usually being cancelled. An
    adapter's stream counts nothing until its end and is left out."""
    try:
        snapshot = stream.current_message_snapshot
        spent = snapshot.usage
        written = sum(
            len(getattr(block, "text", None) or "") + len(getattr(block, "thinking", None) or "")
            for block in snapshot.content
        )
        tokens = wire.Usage(
            input_tokens=spent.input_tokens or 0,
            output_tokens=max(spent.output_tokens or 0, written // 4),
            cache_read_input_tokens=getattr(spent, "cache_read_input_tokens", None) or 0,
            cache_creation_input_tokens=getattr(spent, "cache_creation_input_tokens", None) or 0,
        )
        model = asked if route.own else snapshot.model
    except Exception:
        # Left before anything arrived: nothing is known to have been spent.
        return
    task = asyncio.get_running_loop().create_task(
        usage.record_claude(purpose, model, tokens, provider=route.provider, own=route.own)
    )
    _settling.add(task)
    task.add_done_callback(_settling.discard)


async def voyage_embed(texts: list[str], *, input_type: str) -> list[list[float]]:
    route = await routing.voyage()
    model = get_settings().voyage_embed_model
    voyage = _voyage(route)
    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        async with _call(route):
            result = await voyage.embed(
                texts=texts[start : start + EMBED_BATCH],
                model=model,
                input_type=input_type,
                truncation=True,
            )
        out.extend(cast(list[list[float]], result.embeddings))
        await usage.record_voyage(f"embed_{input_type}", model, result.total_tokens, own=route.own)
    return out


async def voyage_rerank(query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
    route = await routing.voyage()
    model = get_settings().voyage_rerank_model
    async with _call(route):
        result = await _voyage(route).rerank(
            query=query, documents=documents, model=model, top_k=top_k
        )
    await usage.record_voyage("rerank", model, result.total_tokens, own=route.own)
    return [(r.index, r.relevance_score) for r in result.results]
