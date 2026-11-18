"""The providers a user can bring their own key for, and each one's models.

The app calls a model for three jobs, each set by one of the server's own
models (app/config.py): reading a save ("analysis", the fast cheap one),
writing the wiki ("synthesis"), and answering ("ask", the best one). A
provider lists the models offered for those jobs and which one each job uses
until the user picks another. Every model here has a price in app/pricing.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Role = Literal["analysis", "synthesis", "ask"]
ROLES: tuple[Role, ...] = get_args(Role)


@dataclass(frozen=True)
class Model:
    id: str
    name: str


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    # What a key looks like, and where to make one.
    placeholder: str
    help: str
    models: tuple[Model, ...]
    defaults: dict[Role, str]
    # Its key belongs to an AWS region.
    region: bool = False


def _defaults(analysis: str, synthesis: str, ask: str) -> dict[Role, str]:
    return {"analysis": analysis, "synthesis": synthesis, "ask": ask}


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            id="anthropic",
            name="Anthropic",
            placeholder="sk-ant-…",
            help="Create a key at console.anthropic.com under API keys.",
            models=(
                Model("claude-haiku-5-5", "Claude Haiku 5.5"),
                Model("claude-sonnet-5-5", "Claude Sonnet 5.5"),
                Model("claude-opus-5-5", "Claude Opus 5.5"),
            ),
            defaults=_defaults("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"),
        ),
        Provider(
            id="openai",
            name="OpenAI",
            placeholder="sk-…",
            help="Create a key at platform.openai.com under API keys.",
            models=(
                Model("gpt-6-luna", "GPT-6 Luna"),
                Model("gpt-5.4-mini", "GPT-5.4 Mini"),
                Model("gpt-6.1-sol", "GPT-6.1 Sol"),
                Model("gpt-6-astra", "GPT-6 Astra"),
            ),
            defaults=_defaults("gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-sol"),
        ),
        Provider(
            id="gemini",
            name="Google Gemini",
            placeholder="AIza…",
            help="Create a key at aistudio.google.com under Get API key.",
            models=(
                Model("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
                Model("gemini-3.8-flash", "Gemini 3.8 Flash"),
                Model("gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
            ),
            defaults=_defaults("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.8-flash"),
        ),
        Provider(
            id="openrouter",
            name="OpenRouter",
            placeholder="sk-or-…",
            help="Create a key at openrouter.ai under Keys.",
            models=(
                Model("anthropic/claude-haiku-5.5", "Claude Haiku 5.5"),
                Model("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5"),
                Model("anthropic/claude-opus-5.5", "Claude Opus 5.5"),
                Model("openai/gpt-6-luna", "GPT-6 Luna"),
                Model("openai/gpt-6.1-sol", "GPT-6.1 Sol"),
                Model("google/gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
                Model("google/gemini-3.8-flash", "Gemini 3.8 Flash"),
            ),
            defaults=_defaults(
                "anthropic/claude-haiku-5.5",
                "anthropic/claude-sonnet-5.5",
                "anthropic/claude-opus-5.5",
            ),
        ),
        Provider(
            id="bedrock",
            name="Amazon Bedrock",
            placeholder="ABSK…",
            help="Create a Bedrock API key in the AWS console under Bedrock, API keys, "
            "in a region where Claude is enabled for your account.",
            models=(
                Model("anthropic.claude-haiku-5-5", "Claude Haiku 5.5"),
                Model("anthropic.claude-sonnet-5-5", "Claude Sonnet 5.5"),
                Model("anthropic.claude-opus-5-5", "Claude Opus 5.5"),
            ),
            defaults=_defaults(
                "anthropic.claude-haiku-5-5",
                "anthropic.claude-sonnet-5-5",
                "anthropic.claude-opus-5-5",
            ),
            region=True,
        ),
    )
}

DEFAULT_REGION = "us-east-1"


def name(provider: str) -> str:
    known = PROVIDERS.get(provider)
    return known.name if known else provider


def chosen(provider: str, picked: dict[str, dict[str, str]] | None) -> dict[Role, str]:
    """The model each job uses on `provider`: the user's pick while the
    provider still offers it, else the default."""
    known = PROVIDERS[provider]
    offered = {m.id for m in known.models}
    mine = (picked or {}).get(provider) or {}
    return {
        role: mine[role] if mine.get(role) in offered else known.defaults[role] for role in ROLES
    }
