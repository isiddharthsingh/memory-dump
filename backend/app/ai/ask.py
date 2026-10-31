from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.clients import claude_json, claude_stream
from app.ai.retrieval import SOURCES, Candidate, Filters, Scope, hybrid_search
from app.config import get_settings
from app.db.models import Item, TopicPage

log = logging.getLogger(__name__)

ASK_SYSTEM = """You answer questions about the user's personal saved \
content (videos, tweets, articles, Instagram posts, PDFs, images) using \
ONLY the provided context chunks.

Rules:
- Be concise. 1-4 sentences for simple questions; bullets for lists.
- ALWAYS cite the source item for every claim using the format \
  `[item:UUID]` for raw items or `[wiki:slug]` for synthesized pages.
  Put citations inline right after the claim they support.
- Each chunk says what kind of item it comes from and the day the user \
  saved it (for a wiki page, the day it was last updated): use them for \
  questions about when something was saved or what kind of thing it was.
- Earlier questions and answers, when there are any, tell you what a \
  follow-up refers to.
- If the context doesn't answer the question, say "I don't have \
  anything saved about that" — do not invent.
- Prefer the user's own framing/words where helpful.
"""

# Earlier turns a follow-up sends along, to Claude and to the planner, each
# question and answer cut to TURN_CHARS.
HISTORY_TURNS = 6
TURN_CHARS = 4_000
# Chunks in an answer's context, at most PER_SOURCE of them from one item or
# page, so the context holds at least three sources.
CONTEXT_CHUNKS = 6
PER_SOURCE = 2
# The answer thinks first, and thinking counts toward max_tokens.
ANSWER_MAX_TOKENS = 8000
# A planner slower than this is skipped: the question is searched as asked.
PLAN_TIMEOUT_S = 10.0

PLAN_SYSTEM = """You prepare a search of the user's library of saved \
things: web pages, posts on X (Twitter), Instagram posts and reels, YouTube \
videos, Reddit posts, PDFs and images, and wiki pages written from them.

From the latest question, and the conversation before it when there is one, \
return:
- query: the latest question as a search query that stands on its own. \
Replace references to the conversation ("the second one", "he", "that \
video") with the names and topics they refer to. Keep proper nouns, handles \
and distinctive words. Leave out words that only say when something was \
saved or what kind of item it is. If nothing is left (as in "what did I save \
yesterday?"), describe what is asked for in a few words, like "saved items".
- source: only when the question asks for one kind of item: "twitter" (a \
tweet, a post on X), "instagram" (a reel, an Instagram post), "youtube", \
"reddit", "web" (an article, a web page, a blog post), "pdf", "image" (a \
photo, a screenshot, an uploaded picture). Otherwise null.
- since and until: only when the question limits when the item was saved, \
as YYYY-MM-DD dates, both included, worked out from today's date: \
"yesterday" is since = until = yesterday, "last week" the 7 days before \
today, "this month" from the 1st to today. Otherwise null.
"""

_NULLABLE_DATE = {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]}
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "source": {"anyOf": [{"type": "string", "enum": list(SOURCES)}, {"type": "null"}]},
        "since": _NULLABLE_DATE,
        "until": _NULLABLE_DATE,
    },
    "required": ["query", "source", "since", "until"],
    "additionalProperties": False,
}

# How each source reads in the context.
_SOURCE_NAMES = {
    "twitter": "a post on X (Twitter)",
    "instagram": "an Instagram post",
    "youtube": "a YouTube video",
    "reddit": "a Reddit post",
    "web": "a web page",
    "pdf": "a PDF",
    "image": "an image",
}


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


@dataclass(frozen=True)
class Turn:
    """An earlier question in the chat and its answer."""

    question: str
    answer: str


@dataclass(frozen=True)
class Plan:
    query: str
    filters: Filters


CITATION_RE = re.compile(r"\[(item|wiki):([A-Za-z0-9_\-]+)\]")


def zone(name: str | None) -> tzinfo:
    """The user's time zone from its IANA name, as the app sends it; UTC when
    there is none or it's unknown. Days ("today", "saved yesterday") are the
    user's, not the server's."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            log.info("unknown time zone %r, using UTC", name)
    return UTC


def _day(at: datetime | None, tz: tzinfo) -> str:
    return at.astimezone(tz).date().isoformat() if at else "an unknown day"


def _format_context(chunks: list[Candidate], tz: tzinfo) -> str:
    blocks = []
    for c in chunks:
        if c.kind == "item":
            head = f"[item:{c.parent_id}] {c.title or '(untitled)'}"
            if c.url:
                head += f" — {c.url}"
            kind = _SOURCE_NAMES.get(c.source or "", "an item")
            head += f"\n{kind[0].upper()}{kind[1:]}, saved {_day(c.at, tz)}"
        else:
            head = f"[wiki:{c.slug}] {c.title or '(untitled)'}"
            head += f"\nA wiki page, updated {_day(c.at, tz)}"
        blocks.append(f"{head}\n{c.text}")
    return "\n\n---\n\n".join(blocks)


def _recent(history: Sequence[Turn]) -> list[Turn]:
    """The last turns that have an answer (one that failed has nothing to
    follow up on, and Claude takes no empty messages), each cut to
    TURN_CHARS: the app sends answers whole, and a long chat would otherwise
    make every question cost as much as all of it."""
    answered = [t for t in history if t.question.strip() and t.answer.strip()]
    return [
        Turn(question=t.question[:TURN_CHARS], answer=t.answer[:TURN_CHARS])
        for t in answered[-HISTORY_TURNS:]
    ]


async def resolve_scope(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    item: uuid.UUID | None = None,
    topic: str | None = None,
) -> Scope | None:
    """What an Ask about `item`, or about the wiki page `topic` and its
    sources, searches. Raises LookupError when the user has no such item or
    page, so the endpoint can answer 404 before anything streams."""
    if item is None and topic is None:
        return None
    if item is not None:
        found = await session.scalar(
            select(Item.id).where(Item.id == item).where(Item.user_id == user_id)
        )
        if found is None:
            raise LookupError("Item not found")
        scope = Scope(item_ids=(item,))
    else:
        page = (
            await session.execute(
                select(TopicPage.id, TopicPage.source_item_ids)
                .where(TopicPage.slug == topic)
                .where(TopicPage.user_id == user_id)
            )
        ).first()
        if page is None:
            raise LookupError("Topic page not found")
        # Retrieval only ever reads the user's own chunks, so a cited item
        # that is gone (or never was theirs) just finds nothing.
        scope = Scope(item_ids=tuple(page.source_item_ids or ()), page_ids=(page.id,))
    # Planning and answering come next: hold no connection while they run.
    await session.commit()
    return scope


def _read_plan(out: dict[str, Any], question: str, tz: tzinfo) -> Plan:
    def day(value: Any) -> date | None:
        try:
            return date.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            return None

    query = out.get("query")
    source = out.get("source")
    since, until = day(out.get("since")), day(out.get("until"))
    if since and until and since > until:
        since, until = until, since
    return Plan(
        query=query.strip() if isinstance(query, str) and query.strip() else question,
        filters=Filters(
            source=source if source in SOURCES else None, since=since, until=until, tz=tz
        ),
    )


async def plan_query(question: str, history: Sequence[Turn], today: date, tz: tzinfo = UTC) -> Plan:
    """The question as a search query that stands on its own (a follow-up
    like "what did the second one say?" finds nothing by itself), and what it
    says about the source and save date of the items it wants, as days in
    `tz`. When planning fails, the question is searched as asked."""
    lines = [f"Today is {today.isoformat()} ({tz})."]
    if history:
        # The citation markers mean nothing to a search.
        convo = "\n\n".join(
            f"User: {t.question}\nAssistant: {CITATION_RE.sub('', t.answer)}" for t in history
        )
        lines.append(f"Conversation so far:\n\n{convo}")
    lines.append(f"Latest question: {question}")
    try:
        async with asyncio.timeout(PLAN_TIMEOUT_S):
            out = await claude_json(
                purpose="ask_plan",
                schema=PLAN_SCHEMA,
                effort="low",
                model=get_settings().claude_analysis_model,
                max_tokens=2048,
                system=PLAN_SYSTEM,
                messages=[{"role": "user", "content": "\n\n".join(lines)}],
            )
    except Exception:
        log.warning("query planning failed, searching with the question as asked", exc_info=True)
        return Plan(query=question, filters=Filters())
    return _read_plan(out, question, tz)


async def retrieve(
    session: AsyncSession,
    question: str,
    user_id: uuid.UUID,
    *,
    history: Sequence[Turn] = (),
    scope: Scope | None = None,
    today: date | None = None,
    tz: tzinfo = UTC,
) -> list[Candidate]:
    """The context for an answer: the planned query's best chunks, searched
    with the plan's filters, and without them when they leave nothing.
    `history` is what `_recent` kept of the chat."""
    plan = await plan_query(question, history, today or datetime.now(tz).date(), tz)
    # An Ask about one item wants that item's best passages, however many.
    one_item = scope is not None and not scope.page_ids and len(scope.item_ids) == 1

    async def search(filters: Filters | None) -> list[Candidate]:
        return await hybrid_search(
            session,
            plan.query,
            user_id,
            final_limit=CONTEXT_CHUNKS,
            scope=scope,
            filters=filters,
            per_parent=None if one_item else PER_SOURCE,
        )

    chunks = await search(plan.filters or None)
    if not chunks and plan.filters:
        chunks = await search(None)
    return chunks


def _messages(
    question: str, history: Sequence[Turn], chunks: list[Candidate], today: date, tz: tzinfo
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for t in history:
        messages.append({"role": "user", "content": t.question})
        messages.append({"role": "assistant", "content": t.answer})
    context = _format_context(chunks, tz)
    messages.append(
        {
            "role": "user",
            "content": f"Today is {today.isoformat()} ({tz}).\n\nContext:\n\n{context}"
            f"\n\nQuestion: {question}\n\nAnswer with citations:",
        }
    )
    return messages


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
            select(Item).where(Item.id.in_(cited_item_ids)).where(Item.user_id == user_id)
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
        pages = await session.execute(
            select(TopicPage)
            .where(TopicPage.slug.in_(cited_slugs))
            .where(TopicPage.user_id == user_id)
        )
        for tp in pages.scalars():
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
    session: AsyncSession,
    question: str,
    user_id: uuid.UUID,
    *,
    history: Sequence[Turn] = (),
    scope: Scope | None = None,
    tz: tzinfo = UTC,
) -> AsyncIterator[StreamToken | StreamDone]:
    """Stream Claude tokens as they arrive, then emit citations at the end."""
    today = datetime.now(tz).date()
    history = _recent(history)
    chunks = await retrieve(
        session, question, user_id, history=history, scope=scope, today=today, tz=tz
    )
    if not chunks:
        yield StreamToken(text="I don't have anything saved about that.")
        yield StreamDone(citations=[])
        return

    settings = get_settings()
    pieces: list[str] = []
    async with claude_stream(
        purpose="ask",
        timeout_s=settings.ask_timeout_s,
        effort="medium",
        model=settings.claude_ask_model,
        max_tokens=ANSWER_MAX_TOKENS,
        system=ASK_SYSTEM,
        messages=_messages(question, history, chunks, today, tz),
    ) as stream:
        async for text in stream.text_stream:
            pieces.append(text)
            yield StreamToken(text=text)

    answer = "".join(pieces).strip()
    citations = await _collect_citations(session, answer, chunks, user_id)
    yield StreamDone(citations=citations)


async def ask(
    session: AsyncSession,
    question: str,
    user_id: uuid.UUID,
    *,
    history: Sequence[Turn] = (),
    scope: Scope | None = None,
    tz: tzinfo = UTC,
) -> AskResult:
    today = datetime.now(tz).date()
    history = _recent(history)
    chunks = await retrieve(
        session, question, user_id, history=history, scope=scope, today=today, tz=tz
    )
    if not chunks:
        return AskResult(
            answer="I don't have anything saved about that.",
            citations=[],
        )

    settings = get_settings()
    # Streamed, then read whole: the answer thinks first, and ASK_TIMEOUT_S
    # bounds each read of a stream but the whole of a plain call.
    async with claude_stream(
        purpose="ask",
        timeout_s=settings.ask_timeout_s,
        effort="medium",
        model=settings.claude_ask_model,
        max_tokens=ANSWER_MAX_TOKENS,
        system=ASK_SYSTEM,
        messages=_messages(question, history, chunks, today, tz),
    ) as stream:
        resp = await stream.get_final_message()
    answer = "".join(b.text for b in resp.content if b.type == "text").strip()
    citations = await _collect_citations(session, answer, chunks, user_id)
    return AskResult(answer=answer, citations=citations)
