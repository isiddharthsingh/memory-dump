"""Model calls made with a user's own Google Gemini key.

Takes a request in the Anthropic Messages shape (what every call site
builds), makes it against the Gemini API's `generateContent` or
`streamGenerateContent`, and answers with the types in app/ai/wire.py, so a
call site reads the answer as it reads Claude's.

What doesn't map one to one:
- Gemini names a function's result by the function, not by the call, so a
  `tool_result` takes its name from the call it answers in the turn before.
- Gemini 3 signs the function calls it makes (`thoughtSignature`, beside
  `functionCall` in the part) and rejects a turn sent back without the
  signature, so each call keeps its part (`ToolUse.raw`) and is echoed as it
  came.
- Thinking is billed as output but counted apart from the answer's tokens.

The key travels only in the `x-goog-api-key` header, never in a URL.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.ai import wire

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# How long the key check waits for the list of models.
CHECK_TIMEOUT_S = 15.0

# Gemini 3 takes low, medium or high.
_THINKING_LEVELS = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}
_MEDIA = ("image", "document")
_STOP_REASONS: dict[str, wire.StopReason] = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "BLOCKLIST": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "SPII": "refusal",
}


def _headers(key: str) -> dict[str, str]:
    return {"x-goog-api-key": key}


def _timeout(timeout_s: float) -> httpx.Timeout:
    return httpx.Timeout(timeout_s, connect=10)


def _error(provider: str, response: httpx.Response) -> wire.ProviderError:
    """A non-2xx answer, whose body has been read."""
    error = wire.refused(provider, response)
    # Google answers a key it doesn't know with 400 (INVALID_ARGUMENT, reason
    # API_KEY_INVALID), where every other provider says 401; say the same,
    # so the user is told to check the key and not the request.
    if error.status_code == 400 and "API_KEY_INVALID" in error.message:
        return wire.ProviderError(provider, 401, error.message, error.retry_after)
    return error


def _function_response(block: dict[str, Any], calls: dict[str, wire.ToolUse]) -> dict[str, Any]:
    call = calls.get(block["tool_use_id"])
    if call is None:
        raise ValueError(f"tool_result {block['tool_use_id']} answers no call in the turn before")
    key = "error" if block.get("is_error") else "result"
    response: dict[str, Any] = {"name": call.name, "response": {key: block["content"]}}
    # Gemini matches results to calls by id when it gave the call one; an id
    # made up here (`_Answer.add`) means nothing to it.
    given = ((call.raw or {}).get("functionCall") or {}).get("id")
    if given:
        response["id"] = given
    return {"functionResponse": response}


def _user_parts(content: str | list[Any], calls: dict[str, wire.ToolUse]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict[str, Any]] = []
    for block in content:
        kind = block["type"]
        if kind == "text":
            parts.append({"text": block["text"]})
        elif kind in _MEDIA and block["source"]["type"] == "base64":
            source = block["source"]
            parts.append({"inlineData": {"mimeType": source["media_type"], "data": source["data"]}})
        elif kind == "tool_result":
            parts.append(_function_response(block, calls))
        else:
            raise ValueError(f"can't send a {kind} block to Gemini")
    return parts


def _model_parts(content: str | list[Any]) -> tuple[list[dict[str, Any]], dict[str, wire.ToolUse]]:
    """An assistant turn as Gemini's parts, and its calls by id for the
    results that follow."""
    if isinstance(content, str):
        return [{"text": content}], {}
    parts: list[dict[str, Any]] = []
    calls: dict[str, wire.ToolUse] = {}
    for block in content:
        if block.type == "text":
            parts.append({"text": block.text})
        elif block.type == "tool_use":
            calls[block.id] = block
            parts.append(block.raw or {"functionCall": {"name": block.name, "args": block.input}})
    return parts, calls


def _contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    calls: dict[str, wire.ToolUse] = {}
    for message in messages:
        if message["role"] == "assistant":
            role = "model"
            parts, calls = _model_parts(message["content"])
        else:
            role = "user"
            parts, calls = _user_parts(message["content"], calls), {}
        # Gemini takes no turn without parts.
        if parts:
            contents.append({"role": role, "parts": parts})
    return contents


def _body(params: dict[str, Any]) -> dict[str, Any]:
    """The request as `generateContent` takes it. Keys Gemini has nothing for
    (`betas`, `fallbacks`) are left out."""
    config: dict[str, Any] = {"maxOutputTokens": params["max_tokens"]}
    output = params.get("output_config") or {}
    if output.get("format"):
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = output["format"]["schema"]
    level = _THINKING_LEVELS.get(output.get("effort") or "")
    # Models before Gemini 3 answer a thinking level with an error.
    if level and params["model"].startswith("gemini-3"):
        config["thinkingConfig"] = {"thinkingLevel": level}

    body: dict[str, Any] = {
        "contents": _contents(params["messages"]),
        "generationConfig": config,
    }
    if params.get("system"):
        body["systemInstruction"] = {"parts": [{"text": params["system"]}]}
    if params.get("tools"):
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parametersJsonSchema": tool["input_schema"],
                    }
                    for tool in params["tools"]
                ]
            }
        ]
    return body


class _Answer:
    """One answer, put together from one response or from a stream's chunks."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.text: list[str] = []
        self.calls: list[wire.ToolUse] = []
        self.finish: str | None = None
        self.blocked = False
        self.answered = False
        self.usage: dict[str, Any] = {}

    def add(self, data: dict[str, Any]) -> str:
        """Takes in a response or a chunk and returns the text it brought."""
        if "error" in data:
            # A stream that fails after its 200 says so in a chunk.
            error = data["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise wire.ProviderError(
                self.provider, code if isinstance(code, int) else None, json.dumps(error)
            )
        self.model = data.get("modelVersion") or self.model
        # The last chunks carry the counts; the latest is the whole answer's.
        self.usage = data.get("usageMetadata") or self.usage
        if (data.get("promptFeedback") or {}).get("blockReason"):
            self.blocked = True
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        self.answered = True
        candidate = candidates[0]
        self.finish = candidate.get("finishReason") or self.finish
        new: list[str] = []
        for part in (candidate.get("content") or {}).get("parts") or []:
            call = part.get("functionCall")
            if call:
                self.calls.append(
                    wire.ToolUse(
                        id=call.get("id") or f"call_{uuid.uuid4().hex}",
                        name=call["name"],
                        input=call.get("args") or {},
                        raw=part,
                    )
                )
            elif part.get("text") and not part.get("thought"):
                new.append(part["text"])
        self.text.extend(new)
        return "".join(new)

    def _stop_reason(self) -> wire.StopReason:
        if self.calls:
            return "tool_use"
        if self.blocked or not self.answered:
            return "refusal"
        return _STOP_REASONS.get(self.finish or "", "end_turn")

    def message(self) -> wire.Message:
        content: list[wire.Text | wire.ToolUse] = []
        text = "".join(self.text)
        if text:
            content.append(wire.Text(text=text))
        content.extend(self.calls)
        cached = int(self.usage.get("cachedContentTokenCount") or 0)
        return wire.Message(
            content=content,
            stop_reason=self._stop_reason(),
            usage=wire.Usage(
                # Google counts the cached tokens in the prompt's.
                input_tokens=max(0, int(self.usage.get("promptTokenCount") or 0) - cached),
                # It bills thoughts as output and counts them apart.
                output_tokens=int(self.usage.get("candidatesTokenCount") or 0)
                + int(self.usage.get("thoughtsTokenCount") or 0),
                cache_read_input_tokens=cached,
            ),
            model=self.model,
        )


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """The JSON of each server-sent event: its `data:` lines, up to a blank one."""
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line.strip() and data:
            yield json.loads("\n".join(data))
            data = []
    if data:
        yield json.loads("\n".join(data))


class _Stream:
    """A streamed answer. Both ways of reading it draw on one iterator, so
    `get_final_message` picks up wherever `text_stream` was left."""

    def __init__(self, provider: str, response: httpx.Response, model: str) -> None:
        self._provider = provider
        self._response = response
        self._answer = _Answer(provider, model)
        self._text = self._read()

    async def _read(self) -> AsyncIterator[str]:
        try:
            async for event in _events(self._response):
                text = self._answer.add(event)
                if text:
                    yield text
        except httpx.HTTPError as exc:
            raise wire.failure(self._provider, exc) from exc

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._text

    async def get_final_message(self) -> wire.Message:
        async for _ in self._text:
            pass
        return self._answer.message()


async def _call(
    provider: str, key: str, method: str, url: str, *, timeout_s: float, **request: Any
) -> dict[str, Any]:
    try:
        response = await wire.http().request(
            method, url, headers=_headers(key), timeout=_timeout(timeout_s), **request
        )
    except httpx.HTTPError as exc:
        raise wire.failure(provider, exc) from exc
    if not response.is_success:
        raise _error(provider, response)
    data: dict[str, Any] = response.json()
    return data


async def create(
    provider: str, key: str, params: dict[str, Any], *, timeout_s: float
) -> wire.Message:
    """One `generateContent` call."""
    data = await _call(
        provider,
        key,
        "POST",
        f"{BASE_URL}/models/{params['model']}:generateContent",
        timeout_s=timeout_s,
        json=_body(params),
    )
    answer = _Answer(provider, params["model"])
    answer.add(data)
    return answer.message()


@asynccontextmanager
async def stream(
    provider: str, key: str, params: dict[str, Any], *, timeout_s: float
) -> AsyncIterator[wire.Stream]:
    """A `streamGenerateContent` call, read as server-sent events. Leaving the
    block closes the response."""
    client = wire.http()
    request = client.build_request(
        "POST",
        f"{BASE_URL}/models/{params['model']}:streamGenerateContent",
        params={"alt": "sse"},
        headers=_headers(key),
        json=_body(params),
        timeout=_timeout(timeout_s),
    )
    try:
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        raise wire.failure(provider, exc) from exc
    try:
        if not response.is_success:
            try:
                await response.aread()
            except httpx.HTTPError as exc:
                raise wire.failure(provider, exc) from exc
            raise _error(provider, response)
        yield _Stream(provider, response, params["model"])
    finally:
        await response.aclose()


async def check_key(provider: str, key: str) -> None:
    """Returns when Google takes the key (it lists a model), raises
    `wire.ProviderError` when it doesn't."""
    await _call(
        provider,
        key,
        "GET",
        f"{BASE_URL}/models",
        timeout_s=CHECK_TIMEOUT_S,
        params={"pageSize": 1},
    )
