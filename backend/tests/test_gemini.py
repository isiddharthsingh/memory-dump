"""The Gemini adapter: an Anthropic-shaped request becomes a `generateContent`
call, and Gemini's answer comes back in the app's wire types. Google is a
mock transport; nothing here reaches the network."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.ai import gemini, wire

KEY = "AIza-test-key"
BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-3.8-flash"

Handler = Callable[[httpx.Request], httpx.Response]

# Tools shaped like Ask's (app/ai/ask_tools.py): strict, nullable through
# `anyOf`, with an enum and a date.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": "Search the library.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "source": {
                    "anyOf": [{"type": "string", "enum": ["youtube", "web"]}, {"type": "null"}]
                },
                "since": {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
            },
            "required": ["query", "source", "since"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read",
        "description": "Read one item or page.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
]


class Google:
    """Stands in for the Gemini API and keeps the requests it got."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Handler = lambda request: httpx.Response(200, json=answer("ok"))
        transport = httpx.MockTransport(self._handle)
        monkeypatch.setattr(wire, "http", lambda: httpx.AsyncClient(transport=transport))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def answers(self, *responses: httpx.Response) -> None:
        queue = list(responses)
        self.handler = lambda request: queue.pop(0)

    @property
    def body(self) -> dict[str, Any]:
        sent: dict[str, Any] = json.loads(self.requests[-1].content)
        return sent


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> Google:
    return Google(monkeypatch)


def answer(text: str = "", *, finish: str = "STOP", **extra: Any) -> dict[str, Any]:
    parts = [{"text": text}] if text else []
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        **extra,
    }


def sse(*chunks: dict[str, Any]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk)}\r\n\r\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def request(**params: Any) -> dict[str, Any]:
    return {
        "model": MODEL,
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "Hello"}],
        **params,
    }


async def create(**params: Any) -> wire.Message:
    return await gemini.create("gemini", KEY, request(**params), timeout_s=30)


CALL_PART = {
    "functionCall": {"name": "search", "args": {"query": "ramen"}, "id": "fc_1"},
    "thoughtSignature": "c2lnbmF0dXJl",
}


async def test_a_text_request_and_its_answer(google: Google) -> None:
    google.answers(
        httpx.Response(
            200,
            json=answer(
                "Hi there",
                modelVersion="gemini-3.8-flash-001",
                usageMetadata={
                    "promptTokenCount": 120,
                    "cachedContentTokenCount": 100,
                    "candidatesTokenCount": 7,
                    "thoughtsTokenCount": 30,
                    "totalTokenCount": 157,
                },
            ),
        )
    )
    message = await create(
        system="Be brief.",
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Again", "cache_control": {"type": "ephemeral"}}
                ],
            },
        ],
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    sent = google.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == f"{BASE}/models/{MODEL}:generateContent"
    assert sent.headers["x-goog-api-key"] == KEY
    assert google.body == {
        "systemInstruction": {"parts": [{"text": "Be brief."}]},
        "contents": [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "model", "parts": [{"text": "Hi."}]},
            {"role": "user", "parts": [{"text": "Again"}]},
        ],
        "generationConfig": {"maxOutputTokens": 1000},
    }

    assert message.content == [wire.Text(text="Hi there")]
    assert message.content[0].type == "text"
    assert message.stop_reason == "end_turn"
    assert message.model == "gemini-3.8-flash-001"
    # Cached tokens come out of the prompt's; thoughts are billed as output.
    assert message.usage == wire.Usage(
        input_tokens=20, output_tokens=37, cache_read_input_tokens=100
    )


async def test_the_answer_without_counts_or_a_version(google: Google) -> None:
    google.answers(
        httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Weighing it up", "thought": True},
                                {"text": "One, "},
                                {"text": "two."},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )
    )
    message = await create()
    # Thought summaries aren't the answer; the text parts are one block.
    assert message.content == [wire.Text(text="One, two.")]
    assert message.model == MODEL
    assert message.usage == wire.Usage()


async def test_images_and_pdfs_go_inline(google: Google) -> None:
    await create(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": "aW1n"},
                    },
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "cGRm",
                        },
                    },
                    {"type": "text", "text": "What are these?"},
                ],
            }
        ]
    )
    assert google.body["contents"] == [
        {
            "role": "user",
            "parts": [
                {"inlineData": {"mimeType": "image/jpeg", "data": "aW1n"}},
                {"inlineData": {"mimeType": "application/pdf", "data": "cGRm"}},
                {"text": "What are these?"},
            ],
        }
    ]


async def test_a_json_schema_asks_for_json(google: Google) -> None:
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    }
    google.answers(sse(answer('{"title": '), answer('"Ramen"}')))
    params = request(
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}}
    )
    # As `claude_json` reads it: the final message, without the text stream.
    async with gemini.stream("gemini", KEY, params, timeout_s=30) as stream:
        message = await stream.get_final_message()

    assert google.body["generationConfig"] == {
        "maxOutputTokens": 1000,
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
        "thinkingConfig": {"thinkingLevel": "low"},
    }
    text = "".join(b.text for b in message.content if b.type == "text")
    assert json.loads(text) == {"title": "Ramen"}


@pytest.mark.parametrize(
    ("model", "effort", "level"),
    [
        ("gemini-3.8-flash", "low", "low"),
        ("gemini-3.8-flash", "medium", "medium"),
        ("gemini-3.1-pro-preview", "high", "high"),
        ("gemini-3-flash-preview", "xhigh", "high"),
        ("gemini-3.8-flash", "max", "high"),
        ("gemini-2.5-flash", "medium", None),
        ("gemini-3.8-flash", None, None),
    ],
)
async def test_effort_is_a_thinking_level_on_gemini_3_only(
    google: Google, model: str, effort: str | None, level: str | None
) -> None:
    await create(model=model, output_config={"effort": effort} if effort else None)
    config = google.body["generationConfig"]
    assert config.get("thinkingConfig") == ({"thinkingLevel": level} if level else None)
    assert "temperature" not in config


async def test_tools_round_trip(google: Google) -> None:
    unsigned = {"functionCall": {"name": "read", "args": {"ref": "wiki:ramen"}}}
    google.answers(
        httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "Let me look."}, CALL_PART, unsigned],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        ),
        httpx.Response(200, json=answer("You saved two ramen places.")),
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": "Any ramen?"}]
    first = await create(tools=TOOLS, messages=messages)

    assert google.body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parametersJsonSchema": tool["input_schema"],
                }
                for tool in TOOLS
            ]
        }
    ]
    assert first.stop_reason == "tool_use"
    text, search, read = first.content
    assert text == wire.Text(text="Let me look.")
    assert isinstance(search, wire.ToolUse) and isinstance(read, wire.ToolUse)
    assert (search.type, search.id, search.name) == ("tool_use", "fc_1", "search")
    assert search.input == {"query": "ramen"}
    assert search.raw == CALL_PART
    # A call Gemini gave no id gets one of ours.
    assert read.id.startswith("call_") and len(read.id) == len("call_") + 32
    assert read.raw == unsigned

    # The turn goes back as Ask sends it: the answer's blocks, then the results.
    messages.append({"role": "assistant", "content": first.content})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": search.id, "content": "[item:1] Ramen"},
                {
                    "type": "tool_result",
                    "tool_use_id": read.id,
                    "content": "No such page.",
                    "is_error": True,
                },
                {"type": "text", "text": "Answer now."},
            ],
        }
    )
    second = await create(tools=TOOLS, messages=messages)

    assert google.body["contents"] == [
        {"role": "user", "parts": [{"text": "Any ramen?"}]},
        # The calls as Gemini made them, signature included.
        {"role": "model", "parts": [{"text": "Let me look."}, CALL_PART, unsigned]},
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "search",
                        "id": "fc_1",
                        "response": {"result": "[item:1] Ramen"},
                    }
                },
                # Our id for the second call stays here.
                {"functionResponse": {"name": "read", "response": {"error": "No such page."}}},
                {"text": "Answer now."},
            ],
        },
    ]
    assert second.stop_reason == "end_turn"


async def test_a_call_without_its_part_and_a_result_without_its_call(google: Google) -> None:
    call = wire.ToolUse(id="toolu_1", name="read", input={"ref": "item:1"})
    turn = [{"role": "assistant", "content": [call]}]
    await create(
        messages=[
            *turn,
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "Text"}],
            },
        ]
    )
    assert google.body["contents"] == [
        {"role": "model", "parts": [{"functionCall": {"name": "read", "args": {"ref": "item:1"}}}]},
        {
            "role": "user",
            "parts": [{"functionResponse": {"name": "read", "response": {"result": "Text"}}}],
        },
    ]

    # Gemini can't be told which function a result is from without its call.
    with pytest.raises(ValueError, match="toolu_2"):
        await create(
            messages=[
                *turn,
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "?"}],
                },
            ]
        )
    assert len(google.requests) == 1


STREAMED = (
    answer("Two places: "),
    answer("Ichiran"),
    {"candidates": [{"content": {"role": "model", "parts": [CALL_PART]}}]},
    {
        "candidates": [{"content": {"role": "model", "parts": []}, "finishReason": "STOP"}],
        "modelVersion": "gemini-3.8-flash-001",
        "usageMetadata": {
            "promptTokenCount": 50,
            "candidatesTokenCount": 9,
            "thoughtsTokenCount": 4,
        },
    },
)


def check_streamed(message: wire.Message) -> None:
    text, call = message.content
    assert text == wire.Text(text="Two places: Ichiran")
    assert isinstance(call, wire.ToolUse)
    assert (call.id, call.name, call.input, call.raw) == (
        "fc_1",
        "search",
        {"query": "ramen"},
        CALL_PART,
    )
    assert message.stop_reason == "tool_use"
    assert message.model == "gemini-3.8-flash-001"
    assert message.usage == wire.Usage(input_tokens=50, output_tokens=13)


async def test_a_stream_read_as_text_then_whole(google: Google) -> None:
    google.answers(sse(*STREAMED))
    async with gemini.stream("gemini", KEY, request(tools=TOOLS), timeout_s=30) as stream:
        pieces = [text async for text in stream.text_stream]
        message = await stream.get_final_message()
        assert await stream.get_final_message() == message

    sent = google.requests[0]
    assert str(sent.url) == f"{BASE}/models/{MODEL}:streamGenerateContent?alt=sse"
    assert sent.headers["x-goog-api-key"] == KEY
    assert google.body["contents"] == [{"role": "user", "parts": [{"text": "Hello"}]}]
    assert pieces == ["Two places: ", "Ichiran"]
    check_streamed(message)


async def test_a_stream_read_whole_or_from_part_way(google: Google) -> None:
    google.answers(sse(*STREAMED), sse(*STREAMED))
    async with gemini.stream("gemini", KEY, request(), timeout_s=30) as stream:
        check_streamed(await stream.get_final_message())
        assert [text async for text in stream.text_stream] == []

    async with gemini.stream("gemini", KEY, request(), timeout_s=30) as stream:
        async for text in stream.text_stream:
            assert text == "Two places: "
            break
        # The rest of the response is read for the whole message.
        check_streamed(await stream.get_final_message())


async def test_leaving_the_stream_closes_the_response(google: Google) -> None:
    closed: list[bool] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            yield f"data: {json.dumps(answer('Hi'))}\n\n".encode()

        async def aclose(self) -> None:
            closed.append(True)

    google.answers(httpx.Response(200, stream=Body()))
    async with gemini.stream("gemini", KEY, request(), timeout_s=30):
        assert not closed
    assert closed == [True]


@pytest.mark.parametrize(
    ("finish", "stop_reason"),
    [
        ("STOP", "end_turn"),
        ("MAX_TOKENS", "max_tokens"),
        ("SAFETY", "refusal"),
        ("RECITATION", "refusal"),
        ("BLOCKLIST", "refusal"),
        ("PROHIBITED_CONTENT", "refusal"),
        ("SPII", "refusal"),
        ("MALFORMED_FUNCTION_CALL", "end_turn"),
        ("OTHER", "end_turn"),
    ],
)
async def test_stop_reasons(google: Google, finish: str, stop_reason: str) -> None:
    google.answers(httpx.Response(200, json=answer("Some text", finish=finish)))
    assert (await create()).stop_reason == stop_reason


async def test_a_blocked_prompt_is_a_refusal(google: Google) -> None:
    blocked = {
        "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
        "usageMetadata": {"promptTokenCount": 12},
    }
    google.answers(
        httpx.Response(200, json=blocked),
        httpx.Response(200, json={"candidates": []}),
        sse(blocked),
    )
    message = await create()
    assert (message.content, message.stop_reason) == ([], "refusal")
    assert message.usage.input_tokens == 12
    # No candidate at all reads the same.
    assert (await create()).stop_reason == "refusal"
    async with gemini.stream("gemini", KEY, request(), timeout_s=30) as stream:
        assert (await stream.get_final_message()).stop_reason == "refusal"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
async def test_an_error_answer_keeps_its_status(google: Google, status: int) -> None:
    body = {"error": {"code": status, "message": "API key not valid.", "status": "X"}}
    google.answers(httpx.Response(status, json=body), httpx.Response(status, json=body))

    with pytest.raises(wire.ProviderError) as caught:
        await create()
    error = caught.value
    assert (error.provider, error.status_code, error.retry_after) == ("gemini", status, None)
    assert "API key not valid." in error.message

    # A streamed call's error body is read before it is raised.
    with pytest.raises(wire.ProviderError) as caught:
        async with gemini.stream("gemini", KEY, request(), timeout_s=30):
            pytest.fail("the stream opened")
    assert caught.value.status_code == status
    assert "API key not valid." in caught.value.message


async def test_a_rate_limit_says_how_long_to_wait(google: Google) -> None:
    limited = httpx.Response(429, headers={"Retry-After": "17"}, json={"error": {"code": 429}})
    google.answers(limited, httpx.Response(429, headers={"Retry-After": "17"}, text="slow down"))

    with pytest.raises(wire.ProviderError) as caught:
        await create()
    assert (caught.value.status_code, caught.value.retry_after) == (429, 17.0)

    with pytest.raises(wire.ProviderError) as caught:
        async with gemini.stream("gemini", KEY, request(), timeout_s=30):
            pytest.fail("the stream opened")
    assert (caught.value.status_code, caught.value.retry_after) == (429, 17.0)
    assert caught.value.message == "slow down"


async def test_no_answer_has_no_status(google: Google) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    google.handler = refuse
    with pytest.raises(wire.ProviderError) as caught:
        await create()
    assert (caught.value.provider, caught.value.status_code) == ("gemini", None)
    assert isinstance(caught.value.__cause__, httpx.ConnectError)

    with pytest.raises(wire.ProviderError) as caught:
        async with gemini.stream("gemini", KEY, request(), timeout_s=30):
            pytest.fail("the stream opened")
    assert caught.value.status_code is None

    with pytest.raises(wire.ProviderError) as caught:
        await gemini.check_key("gemini", KEY)
    assert caught.value.status_code is None


async def test_a_stream_that_breaks_part_way(google: Google) -> None:
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            yield f"data: {json.dumps(answer('Half'))}\n\n".encode()
            raise httpx.ReadTimeout("too slow")

    overloaded = {"error": {"code": 503, "message": "The model is overloaded.", "status": "X"}}
    google.answers(httpx.Response(200, stream=Broken()), sse(answer("Half"), overloaded))

    async with gemini.stream("gemini", KEY, request(), timeout_s=30) as stream:
        with pytest.raises(wire.ProviderError) as caught:
            await stream.get_final_message()
    assert caught.value.status_code is None

    # An error Google sends after its 200 isn't an empty answer.
    async with gemini.stream("gemini", KEY, request(), timeout_s=30) as stream:
        with pytest.raises(wire.ProviderError) as caught:
            _ = [text async for text in stream.text_stream]
    assert caught.value.status_code == 503
    assert "overloaded" in caught.value.message


async def test_check_key(google: Google) -> None:
    google.answers(
        httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-flash"}]}),
        httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid."}}),
    )
    assert await gemini.check_key("gemini", KEY) is None
    sent = google.requests[0]
    assert sent.method == "GET"
    assert str(sent.url) == f"{BASE}/models?pageSize=1"
    assert sent.headers["x-goog-api-key"] == KEY

    with pytest.raises(wire.ProviderError) as caught:
        await gemini.check_key("gemini", "wrong")
    assert (caught.value.provider, caught.value.status_code) == ("gemini", 400)


async def test_the_key_is_never_in_a_url_or_an_error(google: Google) -> None:
    google.answers(
        httpx.Response(200, json=answer("ok")),
        sse(answer("ok")),
        httpx.Response(200, json={"models": []}),
        httpx.Response(403, json={"error": {"code": 403, "message": "Denied."}}),
    )
    await create()
    async with gemini.stream("gemini", KEY, request(), timeout_s=30) as stream:
        await stream.get_final_message()
    await gemini.check_key("gemini", KEY)
    with pytest.raises(wire.ProviderError) as caught:
        await create()

    assert len(google.requests) == 4
    for sent in google.requests:
        assert KEY not in str(sent.url)
        assert KEY not in sent.content.decode()
        assert sent.headers["x-goog-api-key"] == KEY
    assert KEY not in str(caught.value)
    assert KEY not in caught.value.message


async def test_a_key_google_doesnt_know_reads_as_unauthorized(google: Google) -> None:
    """Checked live 2026-11-19: an invalid key is a 400 naming API_KEY_INVALID."""
    body = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
            "details": [{"reason": "API_KEY_INVALID", "domain": "googleapis.com"}],
        }
    }
    google.answers(httpx.Response(400, json=body))
    with pytest.raises(wire.ProviderError) as failed:
        await gemini.check_key("gemini", "AIza-wrong")
    assert failed.value.status_code == 401
