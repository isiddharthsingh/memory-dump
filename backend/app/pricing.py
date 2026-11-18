"""What a call cost, in USD, from its tokens and the provider's list price.

The month's free allowance and a user's own spend limit are both counted in
these dollars (app/ai/routing.py), so every model the app can call has a
price here: one it doesn't know is priced like the dearest, never as free.
OpenRouter says what it billed for each call, and that wins over the table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Price:
    """USD per million tokens. A cache read costs `cache_read`; writing a
    5-minute cache entry costs 1.25 times the input rate."""

    input: float
    output: float = 0.0
    cache_read: float | None = None


# List prices checked 2026-11-19 against OpenRouter's public model list, which
# carries each provider's own price, and Voyage's pricing page.
_ANTHROPIC = {
    "claude-fable-5-2": Price(8, 40, 0.20),
    "claude-fable-5-1": Price(10, 50, 0.25),
    "claude-fable-5": Price(10, 50, 0.25),
    "claude-opus-5-5": Price(4, 20, 0.20),
    "claude-opus-5": Price(5, 25, 0.50),
    "claude-opus-4-8": Price(5, 25, 0.50),
    "claude-opus-4-7": Price(5, 25, 0.50),
    "claude-opus-4-6": Price(5, 25, 0.50),
    "claude-opus-4-5": Price(5, 25, 0.50),
    "claude-sonnet-5-5": Price(2, 10, 0.10),
    "claude-sonnet-5": Price(2, 10, 0.20),
    "claude-sonnet-4-6": Price(3, 15, 0.30),
    "claude-sonnet-4-5": Price(3, 15, 0.30),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-haiku-4-5": Price(1, 5, 0.10),
}
_OPENAI = {
    "gpt-6-astra": Price(10, 50, 1),
    "gpt-6.1-sol": Price(2, 10, 0.10),
    "gpt-6-sol": Price(2, 10, 0.20),
    "gpt-6-luna": Price(0.10, 0.50, 0.01),
    "gpt-5.4": Price(2.50, 15, 0.25),
    "gpt-5.4-mini": Price(0.75, 4.50, 0.075),
}
_GEMINI = {
    "gemini-3.8-flash": Price(0.75, 3.75, 0.075),
    "gemini-3.5-flash": Price(1.50, 9, 0.15),
    "gemini-3.5-flash-lite": Price(0.30, 2.50, 0.03),
    "gemini-3.1-pro-preview": Price(2, 12, 0.20),
}
# What a call costs when OpenRouter's answer doesn't say.
_OPENROUTER = {
    "anthropic/claude-opus-5.5": Price(4, 20, 0.20),
    "anthropic/claude-sonnet-5.5": Price(2, 10, 0.10),
    "anthropic/claude-haiku-5.5": Price(0.10, 0.50, 0.01),
    "openai/gpt-6.1-sol": Price(2, 10, 0.10),
    "openai/gpt-6-luna": Price(0.10, 0.50, 0.01),
    "google/gemini-3.8-flash": Price(0.825, 4.125, 0.0825),
    "google/gemini-3.5-flash-lite": Price(0.30, 2.50, 0.03),
}
# Embedding and reranking are priced per token read; there is no output.
_VOYAGE = {
    "voyage-3-large": Price(0.18),
    "voyage-3.5": Price(0.06),
    "voyage-3.5-lite": Price(0.02),
    "rerank-2.5": Price(0.05),
    "rerank-2.5-lite": Price(0.02),
}
_TABLES = {
    "anthropic": _ANTHROPIC,
    "bedrock": _ANTHROPIC,
    "openai": _OPENAI,
    "gemini": _GEMINI,
    "openrouter": _OPENROUTER,
    "voyage": _VOYAGE,
}
# A model no table knows costs what the dearest one does.
_UNKNOWN = {"voyage": Price(0.18)}
_DEAREST = Price(10, 50, 1)
# Bedrock's in-region endpoint charges a tenth more than Anthropic's list.
_BEDROCK_FACTOR = 1.1
_CACHE_WRITE_FACTOR = 1.25

_warned: set[tuple[str, str]] = set()


def price(provider: str, model: str) -> Price:
    """The model's price: its own row, or the row its id starts with, so a
    dated snapshot ("claude-haiku-4-5-20251001") is priced like its family."""
    table = _TABLES.get(provider, {})
    name = model.split("anthropic.", 1)[-1] if provider == "bedrock" else model
    found = table.get(name)
    if found is None:
        starts = [k for k in table if name.startswith(k) and name[len(k)] in "-.:@"]
        found = table[max(starts, key=len)] if starts else None
    if found is None:
        if (provider, model) not in _warned:
            _warned.add((provider, model))
            log.warning(
                "no price for %s model %s; counting it at the dearest rate", provider, model
            )
        return _UNKNOWN.get(provider, _DEAREST)
    if provider == "bedrock":
        return Price(
            found.input * _BEDROCK_FACTOR,
            found.output * _BEDROCK_FACTOR,
            None if found.cache_read is None else found.cache_read * _BEDROCK_FACTOR,
        )
    return found


def cost(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    billed: float | None = None,
) -> Decimal:
    """What the call cost: `billed` when the provider said, else its tokens
    at the list price."""
    if billed is not None:
        return Decimal(str(billed))
    p = price(provider, model)
    per_million = (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_read_tokens * (p.input if p.cache_read is None else p.cache_read)
        + cache_write_tokens * p.input * _CACHE_WRITE_FACTOR
    )
    return Decimal(str(per_million)) / 1_000_000
