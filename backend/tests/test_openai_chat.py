"""The OpenAI / OpenRouter adapter: what it sends for a request in the
Anthropic Messages shape, and how it reads the answer. No network: the
provider is an httpx.MockTransport."""

import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.ai import openai_chat, wire
from app.ai.ask_tools import TOOLS, echo

KEY = "sk-test-key-123"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

Handler = Callable[[httpx.Request], httpx.Response]


def serve(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> list[httpx.Request]:
    """Answers the adapter's requests with `handler` and returns them as they come."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        wire, "http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(record))
    )
    return seen


def answer(
    content: str | None = "Hello",
    *,
    finish: str = "stop",
    usage: dict[str, Any] | None = None,
    **message: Any,
) -> Handler:
    body = {
        "id": "chatcmpl-1",
        "model": "gpt-6.1-sol-2026-10-01",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, **message},
                "finish_reason": finish,
            }
        ],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return lambda request: httpx.Response(200, json=body)


class Body(httpx.AsyncByteStream):
    """A response body that arrives a few bytes at a time, as a stream's
    does: events are cut anywhere, and it stays open until it is closed."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self.data), 17):
            yield self.data[start : start + 17]

    async def aclose(self) -> None:
        self.closed = True


def sse(*chunks: dict[str, Any] | str) -> Handler:
    """A streamed answer: each dict a `data:` event, each str a line as is."""
    lines = [c if isinstance(c, str) else f"data: {json.dumps(c)}" for c in chunks]
    text = "\n\n".join([*lines, "data: [DONE]", ""])
    return lambda request: httpx.Response(
        200, stream=Body(text.encode()), headers={"content-type": "text/event-stream"}
    )


def delta(finish: str | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "model": "gpt-6.1-sol-2026-10-01",
        "choices": [{"index": 0, "delta": fields, "finish_reason": finish}],
    }


def params(**over: Any) -> dict[str, Any]:
    return {
        "model": "gpt-6.1-sol",
        "max_tokens": 2048,
        "system": "Be brief.",
        "messages": [{"role": "user", "content": "Hi"}],
        **over,
    }


def sent(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "read", "arguments": '{"ref": "item:abc"}'},
    "extra_content": {"google": {"thought_signature": "sig"}},
}


async def test_openai_text_request_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 40},
        "completion_tokens_details": {"reasoning_tokens": 12},
    }
    seen = serve(monkeypatch, answer("Hello", usage=usage))
    message = await openai_chat.create(
        "openai",
        KEY,
        params(output_config={"effort": "xhigh"}, betas=["x"], fallbacks="default"),
        timeout_s=30,
    )

    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", OPENAI_URL)
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert "x-title" not in request.headers
    assert request.extensions["timeout"] == {"connect": 10, "read": 30, "write": 30, "pool": 30}
    assert sent(request) == {
        "model": "gpt-6.1-sol",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hi"},
        ],
        "max_completion_tokens": 2048,
        "reasoning_effort": "high",
    }
    assert KEY not in request.content.decode()

    assert message.content == [wire.Text("Hello")]
    assert message.stop_reason == "end_turn"
    assert message.model == "gpt-6.1-sol-2026-10-01"
    assert message.usage == wire.Usage(
        input_tokens=60, output_tokens=20, cache_read_input_tokens=40, cost=None
    )


async def test_openrouter_text_request_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 30,
            "cost": 0.0123,
            "prompt_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
        },
    }
    seen = serve(monkeypatch, lambda request: httpx.Response(200, json=body))
    request_params = params(model="anthropic/claude-sonnet-5.5", output_config={"effort": "max"})
    del request_params["system"]
    message = await openai_chat.create("openrouter", KEY, request_params, timeout_s=30)

    request = seen[0]
    assert str(request.url) == OPENROUTER_URL
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["x-title"] == "Memory Dump"
    assert sent(request) == {
        "model": "anthropic/claude-sonnet-5.5",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 2048,
        "reasoning": {"effort": "high"},
    }
    # The answer named no model, so it is the one asked for.
    assert message.model == "anthropic/claude-sonnet-5.5"
    assert message.usage == wire.Usage(
        input_tokens=300,
        output_tokens=30,
        cache_read_input_tokens=200,
        cache_creation_input_tokens=100,
        cost=0.0123,
    )


@pytest.mark.parametrize(
    ("provider", "model", "kept"),
    [
        ("openrouter", "anthropic/claude-sonnet-5.5", True),
        ("openrouter", "openai/gpt-6.1-sol", False),
        ("openai", "gpt-6.1-sol", False),
    ],
)
async def test_cache_control_only_reaches_anthropic_models(
    monkeypatch: pytest.MonkeyPatch, provider: str, model: str, kept: bool
) -> None:
    seen = serve(monkeypatch, answer())
    content = [
        {"type": "text", "text": "the item", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "the page"},
    ]
    await openai_chat.create(
        provider,
        KEY,
        params(model=model, messages=[{"role": "user", "content": content}]),
        timeout_s=5,
    )
    first = {"type": "text", "text": "the item"}
    if kept:
        first["cache_control"] = {"type": "ephemeral"}  # type: ignore[assignment]
    assert sent(seen[0])["messages"][1] == {
        "role": "user",
        "content": [first, {"type": "text", "text": "the page"}],
    }


async def test_images_and_pdfs(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = serve(monkeypatch, answer())
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "SlBH"}},
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": "UERG"},
        },
        {"type": "text", "text": "Describe these."},
    ]
    await openai_chat.create(
        "openai", KEY, params(messages=[{"role": "user", "content": content}]), timeout_s=5
    )
    assert sent(seen[0])["messages"][1]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,SlBH"}},
        {
            "type": "file",
            "file": {"filename": "document.pdf", "file_data": "data:application/pdf;base64,UERG"},
        },
        {"type": "text", "text": "Describe these."},
    ]


async def test_json_schema_the_way_claude_json_reads_it(monkeypatch: pytest.MonkeyPatch) -> None:
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    }
    seen = serve(
        monkeypatch,
        sse(
            delta(content='{"title": '),
            delta(content='"A page"}'),
            delta("stop"),
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        ),
    )
    config = {"effort": "low", "format": {"type": "json_schema", "schema": schema}}
    async with openai_chat.stream(
        "openai", KEY, params(output_config=config), timeout_s=5
    ) as stream:
        message = await stream.get_final_message()

    body = sent(seen[0])
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "strict": True, "schema": schema},
    }
    assert body["reasoning_effort"] == "low"
    assert "temperature" not in body
    text = "".join(b.text for b in message.content if b.type == "text")
    assert json.loads(text) == {"title": "A page"}
    assert message.stop_reason == "end_turn"
    assert message.usage.input_tokens == 7


async def test_tools_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = serve(monkeypatch, answer("Looking.", finish="tool_calls", tool_calls=[CALL]))
    first = await openai_chat.create("openai", KEY, params(tools=TOOLS), timeout_s=5)

    tools = sent(seen[0])["tools"]
    assert [t["function"]["name"] for t in tools] == ["search", "list_items", "read"]
    assert tools[2] == {
        "type": "function",
        "function": {
            "name": "read",
            "description": TOOLS[2]["description"],
            "strict": True,
            "parameters": TOOLS[2]["input_schema"],
        },
    }
    assert first.stop_reason == "tool_use"
    text, call = first.content
    assert text == wire.Text("Looking.")
    assert isinstance(call, wire.ToolUse)
    assert (call.id, call.name, call.input) == ("call_1", "read", {"ref": "item:abc"})
    assert call.raw == CALL

    # The turn goes back as ask_stream sends it, with a call that has no raw
    # and a block of a type Chat Completions has no place for.
    turn = [
        SimpleNamespace(type="thinking", thinking="hm"),
        *echo(first.content),
        wire.ToolUse(id="call_2", name="search", input={"query": "ramen"}),
    ]
    results = [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "The item."},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "No query.", "is_error": True},
        {"type": "text", "text": "That was the last lookup."},
    ]
    messages = [
        {"role": "user", "content": "What about ramen?"},
        {"role": "assistant", "content": turn},
        {"role": "user", "content": results},
    ]
    await openai_chat.create("openai", KEY, params(tools=TOOLS, messages=messages), timeout_s=5)
    assert sent(seen[1])["messages"][2:] == [
        {
            "role": "assistant",
            "content": "Looking.",
            "tool_calls": [
                CALL,
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"query": "ramen"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "The item."},
        {"role": "tool", "tool_call_id": "call_2", "content": "No query."},
        {"role": "user", "content": [{"type": "text", "text": "That was the last lookup."}]},
    ]


async def test_assistant_turns_without_text_or_as_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = serve(monkeypatch, answer())
    messages = [
        {"role": "user", "content": "One"},
        {"role": "assistant", "content": "An earlier answer."},
        {"role": "user", "content": "Two"},
        {"role": "assistant", "content": [wire.ToolUse(id="c", name="read", input={}, raw=CALL)]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "content": "x"}]},
    ]
    await openai_chat.create("openai", KEY, params(messages=messages), timeout_s=5)
    assert sent(seen[0])["messages"][2:] == [
        {"role": "assistant", "content": "An earlier answer."},
        {"role": "user", "content": "Two"},
        {"role": "assistant", "content": None, "tool_calls": [CALL]},
        {"role": "tool", "tool_call_id": "c", "content": "x"},
    ]


async def test_arguments_that_do_not_parse_are_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"r'}}
    serve(monkeypatch, answer(None, finish="tool_calls", tool_calls=[broken]))
    message = await openai_chat.create("openai", KEY, params(), timeout_s=5)
    (call,) = message.content
    assert isinstance(call, wire.ToolUse)
    assert call.input == {}
    assert call.raw == broken


async def test_effort_with_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI's newer models refuse function tools sent with an effort on
    Chat Completions; OpenRouter takes both."""
    seen = serve(monkeypatch, answer())
    request = params(tools=TOOLS, output_config={"effort": "medium"})
    await openai_chat.create("openai", KEY, request, timeout_s=5)
    await openai_chat.create("openrouter", KEY, request, timeout_s=5)
    assert "reasoning_effort" not in sent(seen[0])
    assert sent(seen[1])["reasoning"] == {"effort": "medium"}


STREAMED = (
    ": OPENROUTER PROCESSING",
    delta(role="assistant", content=""),
    delta(content="Let me "),
    delta(content="look."),
    delta(
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "read", "arguments": ""},
                "extra_content": {"google": {"thought_signature": "sig"}},
            }
        ]
    ),
    delta(tool_calls=[{"index": 0, "function": {"arguments": '{"ref": '}}]),
    delta(
        tool_calls=[
            {"index": 0, "function": {"arguments": '"item:abc"}'}},
            {"index": 1, "id": "call_2", "type": "function", "function": {"name": "search"}},
        ]
    ),
    delta(tool_calls=[{"index": 1, "function": {"arguments": '{"query": "ramen"}'}}]),
    delta("tool_calls"),
    {
        "model": "gpt-6.1-sol-2026-10-01",
        "choices": [],
        "usage": {
            "prompt_tokens": 90,
            "completion_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 30},
        },
    },
)


def check_streamed(message: wire.Message) -> None:
    text, first, second = message.content
    assert text == wire.Text("Let me look.")
    assert isinstance(first, wire.ToolUse) and isinstance(second, wire.ToolUse)
    assert (first.id, first.name, first.input) == ("call_1", "read", {"ref": "item:abc"})
    assert first.raw == CALL
    assert (second.id, second.name, second.input) == ("call_2", "search", {"query": "ramen"})
    assert second.raw == {
        "id": "call_2",
        "type": "function",
        "function": {"name": "search", "arguments": '{"query": "ramen"}'},
    }
    assert message.stop_reason == "tool_use"
    assert message.model == "gpt-6.1-sol-2026-10-01"
    assert message.usage == wire.Usage(
        input_tokens=60, output_tokens=25, cache_read_input_tokens=30
    )


async def test_stream_read_only_for_its_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = serve(monkeypatch, sse(*STREAMED))
    async with openai_chat.stream("openai", KEY, params(tools=TOOLS), timeout_s=30) as stream:
        message = await stream.get_final_message()
    check_streamed(message)

    request = seen[0]
    assert str(request.url) == OPENAI_URL
    assert request.extensions["timeout"] == {"connect": 10, "read": 30, "write": 30, "pool": 30}
    body = sent(request)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


async def test_stream_read_as_text_then_final_message(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, sse(*STREAMED))
    async with openai_chat.stream("openai", KEY, params(tools=TOOLS), timeout_s=30) as stream:
        pieces = [text async for text in stream.text_stream]
        message = await stream.get_final_message()
        assert await stream.get_final_message() is message
    assert pieces == ["Let me ", "look."]
    check_streamed(message)


async def test_stream_final_message_reads_what_text_stream_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, sse(*STREAMED))
    async with openai_chat.stream("openai", KEY, params(tools=TOOLS), timeout_s=30) as stream:
        async for text in stream.text_stream:
            assert text == "Let me "
            break
        message = await stream.get_final_message()
    check_streamed(message)


async def test_openrouter_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenRouter sends usage unasked, in a last chunk that repeats the finish."""
    last = delta("stop", content="")
    last["usage"] = {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.002}
    seen = serve(monkeypatch, sse(delta(content="Hi"), delta("stop"), last))
    async with openai_chat.stream(
        "openrouter", KEY, params(model="google/gemini-3-pro"), timeout_s=5
    ) as stream:
        message = await stream.get_final_message()

    request = seen[0]
    assert str(request.url) == OPENROUTER_URL
    assert request.headers["x-title"] == "Memory Dump"
    body = sent(request)
    assert body["stream"] is True
    assert "stream_options" not in body
    assert body["max_tokens"] == 2048 and "max_completion_tokens" not in body
    assert message.content == [wire.Text("Hi")]
    assert message.stop_reason == "end_turn"
    assert message.usage == wire.Usage(input_tokens=12, output_tokens=4, cost=0.002)


async def test_leaving_the_stream_closes_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    body = Body(b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n\n')
    serve(monkeypatch, lambda request: httpx.Response(200, stream=body))
    async with openai_chat.stream("openai", KEY, params(), timeout_s=5) as stream:
        async for _ in stream.text_stream:
            break
        assert not body.closed
    assert body.closed


@pytest.mark.parametrize(
    ("finish", "message", "expected"),
    [
        ("stop", {}, "end_turn"),
        ("length", {}, "max_tokens"),
        ("tool_calls", {"tool_calls": [CALL]}, "tool_use"),
        ("stop", {"tool_calls": [CALL]}, "tool_use"),
        ("length", {"tool_calls": [CALL]}, "max_tokens"),
        ("content_filter", {}, "refusal"),
        ("stop", {"refusal": "I can't help with that."}, "refusal"),
        ("stop", {"refusal": ""}, "end_turn"),
    ],
)
async def test_stop_reasons(
    monkeypatch: pytest.MonkeyPatch, finish: str, message: dict[str, Any], expected: str
) -> None:
    serve(monkeypatch, answer("Text", finish=finish, **message))
    assert (await openai_chat.create("openai", KEY, params(), timeout_s=5)).stop_reason == expected


async def test_streamed_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, sse(delta(refusal="I can't "), delta(refusal="do that."), delta("stop")))
    async with openai_chat.stream("openai", KEY, params(), timeout_s=5) as stream:
        message = await stream.get_final_message()
    assert message.stop_reason == "refusal"
    assert message.content == []


async def test_reasoning_details_go_back_with_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenRouter keeps a model's reasoning (Gemini's thought signatures,
    Claude's thinking) on the message, and wants it back with the tool results."""
    call = {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
    signed = {"type": "reasoning.text", "text": "Think", "signature": "s1", "format": "f"}
    serve(
        monkeypatch,
        sse(
            delta(reasoning_details=[{"type": "reasoning.text", "text": "Thi", "format": "f"}]),
            delta(reasoning_details=[{"type": "reasoning.text", "text": "nk", "signature": "s1"}]),
            delta(
                reasoning_details=[{"type": "reasoning.encrypted", "data": "xx", "id": "call_1"}]
            ),
            delta(tool_calls=[{"index": 0, **call}]),
            delta("tool_calls"),
        ),
    )
    async with openai_chat.stream(
        "openrouter", KEY, params(model="google/gemini-3-pro"), timeout_s=5
    ) as stream:
        streamed = await stream.get_final_message()
    details = [signed, {"type": "reasoning.encrypted", "data": "xx", "id": "call_1"}]

    seen = serve(
        monkeypatch, answer(None, finish="tool_calls", tool_calls=[call], reasoning_details=details)
    )
    whole = await openai_chat.create("openrouter", KEY, params(), timeout_s=5)

    for message in (streamed, whole):
        (block,) = message.content
        assert isinstance(block, wire.ToolUse) and block.raw == call
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": echo(message.content)},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "x"}],
            },
        ]
        await openai_chat.create("openrouter", KEY, params(messages=messages), timeout_s=5)
        assert sent(seen[-1])["messages"][2] == {
            "role": "assistant",
            "content": None,
            "tool_calls": [call],
            "reasoning_details": details,
        }


@pytest.mark.parametrize("provider", ["openai", "openrouter"])
async def test_a_refused_key_is_a_provider_error(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    body = {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}
    serve(monkeypatch, lambda request: httpx.Response(401, json=body))
    with pytest.raises(wire.ProviderError) as caught:
        await openai_chat.create(provider, KEY, params(), timeout_s=5)
    error = caught.value
    assert (error.provider, error.status_code, error.retry_after) == (provider, 401, None)
    assert "Incorrect API key provided" in error.message
    assert KEY not in str(error) and KEY not in error.message


async def test_rate_limit_carries_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(
        monkeypatch,
        lambda request: httpx.Response(429, text="slow down", headers={"Retry-After": "7"}),
    )
    with pytest.raises(wire.ProviderError) as caught:
        await openai_chat.create("openai", KEY, params(), timeout_s=5)
    assert (caught.value.status_code, caught.value.retry_after) == (429, 7.0)
    assert caught.value.message == "slow down"


async def test_no_answer_is_a_provider_error_without_a_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    serve(monkeypatch, handler)
    with pytest.raises(wire.ProviderError) as caught:
        await openai_chat.create("openai", KEY, params(), timeout_s=5)
    assert caught.value.status_code is None
    with pytest.raises(wire.ProviderError) as caught:
        async with openai_chat.stream("openai", KEY, params(), timeout_s=5):
            pass
    assert caught.value.status_code is None
    with pytest.raises(wire.ProviderError) as caught:
        await openai_chat.check_key("openai", KEY)
    assert caught.value.status_code is None


async def test_a_refused_stream_carries_the_providers_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"error": {"message": "Rate limit reached", "type": "requests"}}
    serve(
        monkeypatch,
        lambda request: httpx.Response(429, json=body, headers={"retry-after": "2.5"}),
    )
    with pytest.raises(wire.ProviderError) as caught:
        async with openai_chat.stream("openrouter", KEY, params(), timeout_s=5):
            pytest.fail("a refused stream has nothing to read")
    error = caught.value
    assert (error.status_code, error.retry_after) == (429, 2.5)
    assert "Rate limit reached" in error.message


async def test_an_error_in_the_middle_of_a_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = delta("error", content="")
    failed["error"] = {"code": 502, "message": "Provider disconnected"}
    serve(monkeypatch, sse(delta(content="Half an "), failed))
    pieces: list[str] = []
    with pytest.raises(wire.ProviderError) as caught:
        async with openai_chat.stream("openrouter", KEY, params(), timeout_s=5) as stream:
            async for text in stream.text_stream:
                pieces.append(text)
    assert pieces == ["Half an "]
    assert caught.value.status_code == 502
    assert "Provider disconnected" in caught.value.message


async def test_an_error_inside_a_200_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error": {"code": 503, "message": "No provider available"}}
    serve(monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(wire.ProviderError) as caught:
        await openai_chat.create("openrouter", KEY, params(), timeout_s=5)
    assert caught.value.status_code == 503


@pytest.mark.parametrize(
    ("provider", "url", "titled"),
    [
        ("openai", "https://api.openai.com/v1/models", False),
        ("openrouter", "https://openrouter.ai/api/v1/key", True),
    ],
)
async def test_check_key(
    monkeypatch: pytest.MonkeyPatch, provider: str, url: str, titled: bool
) -> None:
    seen = serve(monkeypatch, lambda request: httpx.Response(200, json={"data": {}}))
    await openai_chat.check_key(provider, KEY)
    request = seen[0]
    assert (request.method, str(request.url)) == ("GET", url)
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert ("x-title" in request.headers) is titled

    serve(monkeypatch, lambda request: httpx.Response(401, json={"error": {"message": "No"}}))
    with pytest.raises(wire.ProviderError) as caught:
        await openai_chat.check_key(provider, KEY)
    assert (caught.value.provider, caught.value.status_code) == (provider, 401)
