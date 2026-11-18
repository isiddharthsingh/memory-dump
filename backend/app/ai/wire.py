"""What a model call looks like to the rest of the app when it isn't made
through the Anthropic SDK: a user's own OpenAI, OpenRouter or Gemini key
(app/ai/openai_chat.py, app/ai/gemini.py).

Call sites build requests in the Anthropic Messages shape and read answers
the way they read the SDK's (`content` blocks by `type`, `stop_reason`,
`usage`, a stream's `text_stream` and `get_final_message`). The adapters
translate that shape to their provider and answer with these types, so no
call site knows which provider it reached.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

# What `stop_reason` says, whichever provider answered.
StopReason = Literal["end_turn", "max_tokens", "tool_use", "refusal"]


@dataclass
class Text:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    # The call as the provider sent it, sent back unchanged when the turn is
    # echoed with its results (Gemini's thought signature rides on it).
    raw: dict[str, Any] | None = None
    type: Literal["tool_use"] = "tool_use"


@dataclass
class Usage:
    """Tokens as Anthropic counts them: `input_tokens` leaves out what was
    read from a cache, and `output_tokens` includes the model's reasoning."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # What the provider says it billed, in USD, when it says (OpenRouter).
    cost: float | None = None


@dataclass
class Message:
    content: list[Text | ToolUse]
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class Stream(Protocol):
    """A streamed answer. `text_stream` yields the text as it arrives;
    `get_final_message` reads whatever is left and returns the whole answer."""

    @property
    def text_stream(self) -> AsyncIterator[str]: ...

    async def get_final_message(self) -> Message: ...


class ProviderError(Exception):
    """A provider refused or failed a call made with a user's own key.
    `status_code` is None when it never answered (no connection, a timeout).
    `message` is the provider's own text: for the log, never for a client."""

    def __init__(
        self,
        provider: str,
        status_code: int | None,
        message: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{provider} answered {status_code or 'nothing'}: {message[:300]}")
        self.provider = provider
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


_client: tuple[asyncio.AbstractEventLoop, httpx.AsyncClient] | None = None


def http() -> httpx.AsyncClient:
    """The client the adapters call their providers with: one, made again
    when the event loop changes, since its connections belong to the loop
    that opened them. Each call sets its own timeout. Tests replace this."""
    global _client
    loop = asyncio.get_running_loop()
    if _client is None or _client[0] is not loop:
        _client = (loop, httpx.AsyncClient(http2=True, timeout=httpx.Timeout(300, connect=10)))
    return _client[1]


def refused(provider: str, response: httpx.Response) -> ProviderError:
    """A non-2xx answer as the error the app handles, with what the provider
    said (when the body was read) and how long it asked to wait."""
    try:
        wait: float | None = float(response.headers.get("retry-after", ""))
    except ValueError:
        wait = None
    try:
        text = response.text
    except httpx.ResponseNotRead:
        text = ""
    return ProviderError(provider, response.status_code, text, wait)


def failure(provider: str, exc: httpx.HTTPError) -> ProviderError:
    """An httpx failure as the error the app handles."""
    if isinstance(exc, httpx.HTTPStatusError):
        return refused(provider, exc.response)
    return ProviderError(provider, None, repr(exc))
