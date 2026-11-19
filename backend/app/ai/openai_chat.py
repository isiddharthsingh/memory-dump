"""A user's own OpenAI or OpenRouter key: a request in the Anthropic Messages
shape, made against the provider's Chat Completions API and answered with the
types in app/ai/wire.py.

`create` and `stream` read `model`, `max_tokens`, `system`, `messages`, `tools`
and `output_config` from `params` and ignore every other key (`betas`,
`fallbacks`). What the two providers take differently:
- the output cap: `max_completion_tokens` (OpenAI), `max_tokens` (OpenRouter);
- effort: `reasoning_effort` (OpenAI), `reasoning.effort` (OpenRouter);
- `cache_control` on a text part means something only to Anthropic's models,
  which only OpenRouter serves;
- usage in a stream: OpenAI sends it when asked, OpenRouter always.

Every failure is a `wire.ProviderError`. The key goes into the Authorization
header and nowhere else.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai import wire

_BASE = {"openai": "https://api.openai.com/v1", "openrouter": "https://openrouter.ai/api/v1"}
# A GET each provider answers with 200 only for a key that works.
_KEY_CHECK = {"openai": "/models", "openrouter": "/key"}
KEY_CHECK_TIMEOUT_S = 20.0
_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


@dataclass
class _Call(wire.ToolUse):
    """A tool call that remembers its turn's `reasoning_details`. OpenRouter
    wants them back on the assistant message when the turn is echoed with its
    results (Gemini's thought signatures and Claude's thinking live there, not
    on the call), so they ride on the turn's first call."""

    reasoning: list[dict[str, Any]] | None = None


def _headers(provider: str, key: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers["X-Title"] = "Memory Dump"
    return headers


def _data_url(source: dict[str, Any]) -> str:
    return f"data:{source['media_type']};base64,{source['data']}"


def _user(blocks: list[dict[str, Any]], keep_cache: bool) -> list[dict[str, Any]]:
    """A user turn's blocks: a `tool` message per tool result, then the rest
    as one user message."""
    out: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for block in blocks:
        kind = block["type"]
        if kind == "tool_result":
            # Chat Completions has no error flag; the content says what failed.
            out.append(
                {"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]}
            )
        elif kind == "text":
            part = {"type": "text", "text": block["text"]}
            if keep_cache and "cache_control" in block:
                part["cache_control"] = block["cache_control"]
            parts.append(part)
        elif kind == "image":
            parts.append({"type": "image_url", "image_url": {"url": _data_url(block["source"])}})
        elif kind == "document":
            file = {"filename": "document.pdf", "file_data": _data_url(block["source"])}
            parts.append({"type": "file", "file": file})
    if parts:
        out.append({"role": "user", "content": parts})
    return out


def _echoed(call: Any) -> dict[str, Any]:
    """A tool call going back: as the provider sent it, when it was kept."""
    if call.raw is not None:
        return dict(call.raw)
    function = {"name": call.name, "arguments": json.dumps(call.input)}
    return {"id": call.id, "type": "function", "function": function}


def _assistant(blocks: list[Any]) -> dict[str, Any]:
    """An assistant turn echoed back: `wire.Text` and `wire.ToolUse` objects.
    Blocks of any other type (thinking) have no place here."""
    text = "".join(b.text for b in blocks if b.type == "text")
    calls = [_echoed(b) for b in blocks if b.type == "tool_use"]
    # A turn that only calls tools has no content, and one without calls must have some.
    message: dict[str, Any] = {"role": "assistant", "content": text or (None if calls else "")}
    if calls:
        message["tool_calls"] = calls
    reasoning = next((r for b in blocks if (r := getattr(b, "reasoning", None))), None)
    if reasoning:
        message["reasoning_details"] = reasoning
    return message


def _messages(params: dict[str, Any], keep_cache: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if params.get("system"):
        out.append({"role": "system", "content": params["system"]})
    for message in params["messages"]:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
        elif role == "assistant":
            out.append(_assistant(content))
        else:
            out.extend(_user(content, keep_cache))
    return out


def _tool(tool: dict[str, Any]) -> dict[str, Any]:
    function = {k: tool[k] for k in ("name", "description", "strict") if k in tool}
    return {"type": "function", "function": function | {"parameters": tool["input_schema"]}}


def _body(provider: str, params: dict[str, Any], *, streaming: bool) -> dict[str, Any]:
    model = params["model"]
    keep_cache = provider == "openrouter" and model.startswith("anthropic/")
    body: dict[str, Any] = {"model": model, "messages": _messages(params, keep_cache)}
    body["max_completion_tokens" if provider == "openai" else "max_tokens"] = params["max_tokens"]
    if params.get("tools"):
        body["tools"] = [_tool(t) for t in params["tools"]]
    config = params.get("output_config") or {}
    effort = _EFFORT.get(config.get("effort", ""))
    if effort and provider == "openrouter":
        body["reasoning"] = {"effort": effort}
    elif effort and "tools" not in body:
        # OpenAI's models from GPT-5.4 on answer 400 to function tools sent
        # with an effort on this endpoint, so a call with tools runs at the
        # model's default.
        body["reasoning_effort"] = effort
    if config.get("format"):
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "strict": True, "schema": config["format"]["schema"]},
        }
    if streaming:
        body["stream"] = True
        if provider == "openai":
            # OpenRouter ends every stream with its usage, unasked.
            body["stream_options"] = {"include_usage": True}
    return body


def _arguments(text: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(text or "")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage(data: dict[str, Any] | None) -> wire.Usage:
    data = data or {}
    details = data.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") or 0
    cost = data.get("cost")
    return wire.Usage(
        # prompt_tokens counts what was read from the cache; Anthropic's doesn't.
        input_tokens=max((data.get("prompt_tokens") or 0) - cached, 0),
        output_tokens=data.get("completion_tokens") or 0,
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=details.get("cache_write_tokens") or 0,
        cost=float(cost) if isinstance(cost, int | float) else None,
    )


def _stop(finish: str | None, refusal: str | None, called: bool) -> wire.StopReason:
    if refusal or finish == "content_filter":
        return "refusal"
    if finish == "length":
        # Before the calls: one cut off here may be half a call.
        return "max_tokens"
    # Some models finish with "stop" on a turn that calls tools.
    return "tool_use" if called or finish == "tool_calls" else "end_turn"


def _message(
    *,
    text: str,
    refusal: str | None,
    calls: list[dict[str, Any]],
    reasoning: list[dict[str, Any]] | None,
    finish: str | None,
    usage: dict[str, Any] | None,
    model: str,
) -> wire.Message:
    content: list[wire.Text | wire.ToolUse] = []
    if text:
        content.append(wire.Text(text))
    for n, raw in enumerate(calls):
        function = raw.get("function") or {}
        content.append(
            _Call(
                id=raw.get("id") or "",
                name=function.get("name") or "",
                input=_arguments(function.get("arguments")),
                raw=raw,
                reasoning=(reasoning or None) if n == 0 else None,
            )
        )
    return wire.Message(
        content=content,
        stop_reason=_stop(finish, refusal, bool(calls)),
        usage=_usage(usage),
        model=model,
    )


def _reported(provider: str, error: Any) -> wire.ProviderError:
    """An error sent inside a 200 answer or in the middle of a stream.
    OpenRouter's `code` is the HTTP status it stands for; without one, 500."""
    code = error.get("code") if isinstance(error, dict) else None
    return wire.ProviderError(provider, code if isinstance(code, int) else 500, json.dumps(error))


def _add_call(calls: dict[int, dict[str, Any]], part: dict[str, Any]) -> None:
    """Adds a streamed piece of a tool call to the call at its index. The id
    and name arrive once and the arguments in fragments; anything else the
    provider puts on the call (`extra_content`) is kept for the echo."""
    call = calls.setdefault(
        part.get("index") or 0,
        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
    )
    for name, value in part.items():
        if name == "function":
            for key, piece in (value or {}).items():
                if key == "arguments":
                    call["function"]["arguments"] += piece or ""
                elif piece:
                    call["function"][key] = piece
        elif name != "index" and value not in (None, ""):
            call[name] = value


def _add_reasoning(kept: list[dict[str, Any]], details: list[dict[str, Any]]) -> None:
    """Adds a chunk's `reasoning_details` the way OpenRouter's own SDK joins
    them: text runs on until its signature arrives, a summary runs on, and
    every other entry (encrypted) stands alone."""
    for detail in details:
        last = kept[-1] if kept else {}
        kind = detail.get("type")
        if kind == last.get("type") == "reasoning.text" and not last.get("signature"):
            last["text"] = (last.get("text") or "") + (detail.get("text") or "")
            for key in ("signature", "format"):
                if detail.get(key) and not last.get(key):
                    last[key] = detail[key]
        elif kind == last.get("type") == "reasoning.summary":
            last["summary"] = (last.get("summary") or "") + (detail.get("summary") or "")
        else:
            kept.append(dict(detail))


class _Stream:
    """A Chat Completions SSE answer as a `wire.Stream`. `text_stream` and
    `get_final_message` share one reader, so either goes on from where the
    other stopped."""

    def __init__(self, provider: str, model: str, response: httpx.Response) -> None:
        self._provider = provider
        self._model = model
        self._text: list[str] = []
        self._refusal: list[str] = []
        self._calls: dict[int, dict[str, Any]] = {}
        self._reasoning: list[dict[str, Any]] = []
        self._finish: str | None = None
        self._usage: dict[str, Any] | None = None
        self._final: wire.Message | None = None
        self._fragments = self._read(response)

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._fragments

    async def get_final_message(self) -> wire.Message:
        if self._final is None:
            async for _ in self._fragments:
                pass
            self._final = _message(
                text="".join(self._text),
                refusal="".join(self._refusal),
                calls=[self._calls[index] for index in sorted(self._calls)],
                reasoning=self._reasoning,
                finish=self._finish,
                usage=self._usage,
                model=self._model,
            )
        return self._final

    async def _read(self, response: httpx.Response) -> AsyncIterator[str]:
        try:
            async for line in response.aiter_lines():
                # Blank lines end an event; ": …" comments are keepalives.
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                text = self._take(json.loads(data))
                if text:
                    yield text
        except httpx.HTTPError as exc:
            raise wire.failure(self._provider, exc) from exc

    def _take(self, chunk: dict[str, Any]) -> str:
        """Adds one chunk to the answer and returns the text it brought."""
        if chunk.get("error"):
            raise _reported(self._provider, chunk["error"])
        # The usage chunk has no choices (OpenAI) or an empty delta (OpenRouter).
        self._usage = chunk.get("usage") or self._usage
        self._model = chunk.get("model") or self._model
        text = ""
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text += delta.get("content") or ""
            self._refusal.append(delta.get("refusal") or "")
            for part in delta.get("tool_calls") or []:
                _add_call(self._calls, part)
            _add_reasoning(self._reasoning, delta.get("reasoning_details") or [])
            self._finish = choice.get("finish_reason") or self._finish
        self._text.append(text)
        return text


async def create(
    provider: str, key: str, params: dict[str, Any], *, timeout_s: float
) -> wire.Message:
    """One Chat Completions call, answered whole."""
    try:
        response = await wire.http().post(
            f"{_BASE[provider]}/chat/completions",
            headers=_headers(provider, key),
            json=_body(provider, params, streaming=False),
            timeout=httpx.Timeout(timeout_s, connect=10),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise wire.failure(provider, exc) from exc
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    # OpenRouter answers 200 when the model behind it failed, with the error inside.
    error = data.get("error") or choice.get("error")
    if error:
        raise _reported(provider, error)
    message = choice.get("message") or {}
    return _message(
        text=message.get("content") or "",
        refusal=message.get("refusal"),
        calls=message.get("tool_calls") or [],
        reasoning=message.get("reasoning_details"),
        finish=choice.get("finish_reason"),
        usage=data.get("usage"),
        model=data.get("model") or params["model"],
    )


@asynccontextmanager
async def stream(
    provider: str, key: str, params: dict[str, Any], *, timeout_s: float
) -> AsyncIterator[wire.Stream]:
    """A streamed Chat Completions call. Leaving the block closes the response."""
    client = wire.http()
    request = client.build_request(
        "POST",
        f"{_BASE[provider]}/chat/completions",
        headers=_headers(provider, key),
        json=_body(provider, params, streaming=True),
        timeout=httpx.Timeout(timeout_s, connect=10),
    )
    try:
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        raise wire.failure(provider, exc) from exc
    try:
        if not response.is_success:
            try:
                # Read first, so the error carries what the provider said.
                await response.aread()
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise wire.failure(provider, exc) from exc
        yield _Stream(provider, params["model"], response)
    finally:
        await response.aclose()


async def check_key(provider: str, key: str) -> None:
    """Returns when the provider takes `key`; `wire.ProviderError` otherwise."""
    try:
        response = await wire.http().get(
            _BASE[provider] + _KEY_CHECK[provider],
            headers=_headers(provider, key),
            timeout=httpx.Timeout(KEY_CHECK_TIMEOUT_S, connect=10),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise wire.failure(provider, exc) from exc
