from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.clients import claude, claude_stream
from app.ai.retrieval import Candidate, hybrid_search
from app.config import get_settings
from app.db.models import Item, TopicPage

ASK_SYSTEM = """You answer questions about the user's personal saved \
content (videos, tweets, articles, Instagram posts) using ONLY the \
provided context chunks.

Rules:
- Be concise. 1-4 sentences for simple questions; bullets for lists.
- ALWAYS cite the source item for every claim using the format \
  `[item:UUID]` for raw items or `[wiki:slug]` for synthesized pages.
  Put citations inline right after the claim they support.
- If the context doesn't answer the question, say "I don't have \
  anything saved about that" — do not invent.
- Prefer the user's own framing/words where helpful.
"""


@dataclass
class Citation:
    kind: str  # "item" | "topic_page"
    id: uuid.UUID
    title: str | None
    url: str | None
    slug: str | None


@dataclass
class AskResult:
    answer: str
    citations: list[Citation]


CITATION_RE = re.compile(r"\[(item|wiki):([A-Za-z0-9_\-]+)\]")


def _format_context(chunks: list[Candidate]) -> str:
    blocks = []
    for c in chunks:
        if c.kind == "item":
            head = f"[item:{c.parent_id}] {c.title or '(untitled)'}"
            if c.url:
                head += f" — {c.url}"
        else:
            head = f"[wiki:{c.slug}] {c.title or '(untitled)'}"
        blocks.append(f"{head}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


async def _collect_citations(
    session: AsyncSession,
    answer: str,
    chunks: list[Candidate],
    user_id: uuid.UUID,
) -> list[Citation]:
    """Pull citations referenced in the answer, plus the top chunks as fallback.

    All item/topic-page lookups are scoped to `user_id` so a hallucinated
    or stale citation can never reveal another user's content.
    """
    cited_item_ids: set[uuid.UUID] = set()
    cited_slugs: set[str] = set()
    for m in CITATION_RE.finditer(answer):
        kind, ref = m.group(1), m.group(2)
        if kind == "item":
            try:
                cited_item_ids.add(uuid.UUID(ref))
            except ValueError:
                continue
        else:
            cited_slugs.add(ref)

    if not cited_item_ids and not cited_slugs:
        # No explicit citations — return top 3 chunks as suggested sources.
        seen: set[tuple[str, uuid.UUID]] = set()
        result: list[Citation] = []
        for c in chunks[:3]:
            key = (c.kind, c.parent_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                Citation(
                    kind=c.kind,
                    id=c.parent_id,
                    title=c.title,
                    url=c.url,
                    slug=c.slug,
                )
            )
        return result

    citations: list[Citation] = []
    if cited_item_ids:
        rows = await session.execute(
            select(Item)
            .where(Item.id.in_(cited_item_ids))
            .where(Item.user_id == user_id)
        )
        for item in rows.scalars():
            citations.append(
                Citation(
                    kind="item",
                    id=item.id,
                    title=item.title,
                    url=item.url,
                    slug=None,
                )
            )
    if cited_slugs:
        rows = await session.execute(
            select(TopicPage)
            .where(TopicPage.slug.in_(cited_slugs))
            .where(TopicPage.user_id == user_id)
        )
        for tp in rows.scalars():
            citations.append(
                Citation(
                    kind="topic_page",
                    id=tp.id,
                    title=tp.title,
                    url=None,
                    slug=tp.slug,
                )
            )
    return citations


@dataclass
class StreamToken:
    text: str


@dataclass
class StreamDone:
    citations: list[Citation]


async def ask_stream(
    session: AsyncSession, question: str, user_id: uuid.UUID
) -> AsyncIterator[StreamToken | StreamDone]:
    """Stream Claude tokens as they arrive, then emit citations at the end."""
    chunks = await hybrid_search(session, question, user_id, final_limit=6)
    if not chunks:
        yield StreamToken(text="I don't have anything saved about that.")
        yield StreamDone(citations=[])
        return

    settings = get_settings()

    context = _format_context(chunks)
    user = f"Context:\n\n{context}\n\nQuestion: {question}\n\nAnswer with citations:"

    pieces: list[str] = []
    async with claude_stream(
        purpose="ask",
        timeout_s=settings.ask_timeout_s,
        model=settings.claude_ask_model,
        max_tokens=1024,
        system=[
            {"type": "text", "text": ASK_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user}],
    ) as stream:
        async for text in stream.text_stream:
            pieces.append(text)
            yield StreamToken(text=text)

    answer = "".join(pieces).strip()
    citations = await _collect_citations(session, answer, chunks, user_id)
    yield StreamDone(citations=citations)


async def ask(session: AsyncSession, question: str, user_id: uuid.UUID) -> AskResult:
    chunks = await hybrid_search(session, question, user_id, final_limit=6)
    if not chunks:
        return AskResult(
            answer="I don't have anything saved about that.",
            citations=[],
        )

    settings = get_settings()

    context = _format_context(chunks)
    user = f"Context:\n\n{context}\n\nQuestion: {question}\n\nAnswer with citations:"

    resp = await claude(
        purpose="ask",
        timeout_s=settings.ask_timeout_s,
        model=settings.claude_ask_model,
        max_tokens=1024,
        system=[
            {"type": "text", "text": ASK_SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user}],
    )
    answer = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    citations = await _collect_citations(session, answer, chunks, user_id)
    return AskResult(answer=answer, citations=citations)
