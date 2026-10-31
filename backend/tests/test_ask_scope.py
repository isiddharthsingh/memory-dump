"""Ask follow-ups, query planning and scoped questions. Claude and Voyage are
stubbed; the scoped searches run against Postgres."""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from app.ai import ask as ask_module
from app.ai import retrieval
from app.ai.ask import Turn
from app.ai.retrieval import Candidate, Filters, Scope, hybrid_search
from app.config import get_settings
from app.db.models import EMBED_DIM, Item, ItemChunk, TopicPage, TopicPageChunk
from app.db.session import get_sessionmaker
from app.errors import Refused
from app.main import app
from tests.conftest import signup


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def account(client: TestClient) -> Iterator[tuple[dict[str, str], uuid.UUID]]:
    headers = signup(client)
    yield headers, uuid.UUID(client.get("/auth/me", headers=headers).json()["id"])
    client.delete("/auth/me", headers=headers)


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


VEC = [0.1] * EMBED_DIM


@pytest.fixture
def voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every chunk is as near the query as any other; rerank keeps the order."""

    async def embed(query: str) -> list[float]:
        return VEC

    async def rerank(
        query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        return [(i, 1 - i / 100) for i in range(len(documents))][:top_k]

    monkeypatch.setattr(retrieval, "embed_query", embed)
    monkeypatch.setattr(retrieval, "rerank", rerank)


class Claude:
    """Stands in for the planner and the answer, recording what each got."""

    def __init__(self, plan: dict[str, Any] | Exception) -> None:
        self.plan = plan
        self.planned: list[dict[str, Any]] = []
        self.asked: list[dict[str, Any]] = []

    async def claude_json(self, **params: Any) -> dict[str, Any]:
        self.planned.append(params)
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan

    @asynccontextmanager
    async def claude_stream(self, **params: Any) -> AsyncIterator[Any]:
        self.asked.append(params)

        async def text() -> AsyncIterator[str]:
            yield "An answer."

        async def final() -> Any:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="An answer.")])

        yield SimpleNamespace(text_stream=text(), get_final_message=final)

    @property
    def context(self) -> str:
        return str(self.asked[-1]["messages"][-1]["content"])


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> Claude:
    fake = Claude(plan={"query": "spice", "source": None, "since": None, "until": None})
    monkeypatch.setattr(ask_module, "claude_json", fake.claude_json)
    monkeypatch.setattr(ask_module, "claude_stream", fake.claude_stream)
    return fake


def _item(
    db: Any, user_id: uuid.UUID, chunks: int = 1, *, text: str = "spice", **values: Any
) -> uuid.UUID:
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                **{"source": "web", "status": "ready", "title": f"Item {item_id}", **values},
            )
        )
        for n in range(chunks):
            conn.execute(
                insert(ItemChunk.__table__).values(
                    item_id=item_id, user_id=user_id, ord=n, text=f"{text} {n}", embedding=VEC
                )
            )
    return item_id


def _page(db: Any, user_id: uuid.UUID, slug: str, sources: list[uuid.UUID]) -> uuid.UUID:
    page_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(TopicPage.__table__).values(
                id=page_id,
                user_id=user_id,
                slug=slug,
                title=slug.title(),
                body_md="spice",
                source_item_ids=sources,
            )
        )
        conn.execute(
            insert(TopicPageChunk.__table__).values(
                topic_page_id=page_id, user_id=user_id, ord=0, text="spice", embedding=VEC
            )
        )
    return page_id


def _search(client: TestClient, user_id: uuid.UUID, **kwargs: Any) -> list[Candidate]:
    async def run() -> list[Candidate]:
        async with get_sessionmaker()() as session:
            return await hybrid_search(session, "spice", user_id, final_limit=20, **kwargs)

    return client.portal.call(run)  # type: ignore[union-attr]


def _cited(context: str) -> set[str]:
    return {m.group(2) for m in ask_module.CITATION_RE.finditer(context)}


# Follow-ups


def test_a_follow_up_sends_the_earlier_turns_as_messages(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    claude: Claude,
) -> None:
    auth, user_id = account
    _item(db, user_id)
    history = [{"question": f"Question {n}?", "answer": f"Answer {n}."} for n in range(8)]
    # A turn that failed has no answer to follow up on.
    history.append({"question": "Did this fail?", "answer": ""})
    resp = client.post(
        "/v1/ask/stream",
        json={"question": "What did the second one say?", "history": history},
        headers=auth,
    )
    assert resp.status_code == 200
    assert "event: done" in resp.text

    sent = claude.asked[-1]
    # The last six answered turns, oldest first, then the question with its context.
    expected: list[dict[str, str]] = []
    for n in range(2, 8):
        expected += [
            {"role": "user", "content": f"Question {n}?"},
            {"role": "assistant", "content": f"Answer {n}."},
        ]
    assert sent["messages"][:-1] == expected
    assert sent["messages"][-1]["role"] == "user"
    assert "Question: What did the second one say?" in claude.context
    # The planner saw the conversation too.
    planned = claude.planned[-1]["messages"][0]["content"]
    assert "Question 7?" in planned and "Question 1?" not in planned
    assert planned.endswith("Latest question: What did the second one say?")
    # The system prompt is too short to cache; effort is stated.
    assert sent["system"] == ask_module.ASK_SYSTEM
    assert sent["effort"] == "medium"


def test_history_is_optional_and_the_answer_says_today(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    claude: Claude,
) -> None:
    auth, user_id = account
    _item(db, user_id)
    resp = client.post("/v1/ask", json={"question": "Spice?"}, headers=auth)
    assert resp.status_code == 200
    assert len(claude.asked[-1]["messages"]) == 1
    today = datetime.now(UTC).date().isoformat()
    assert claude.context.startswith(f"Today is {today} (UTC).")


# Query planning


class Searches(list[dict[str, Any]]):
    pass


@pytest.fixture
def searches(monkeypatch: pytest.MonkeyPatch) -> Searches:
    """Records each search Ask makes; one with filters finds nothing."""
    calls = Searches()
    hit = Candidate(
        kind="item",
        chunk_id=uuid.uuid4(),
        parent_id=uuid.uuid4(),
        ord=0,
        text="spice",
        title="Spice",
        url=None,
        slug=None,
    )

    async def search(session: Any, query: str, user_id: uuid.UUID, **kw: Any) -> list[Candidate]:
        calls.append({"query": query, **kw})
        return [] if kw.get("filters") else [hit]

    monkeypatch.setattr(ask_module, "hybrid_search", search)
    return calls


async def test_retrieval_searches_the_planned_query_and_drops_filters_that_find_nothing(
    monkeypatch: pytest.MonkeyPatch, searches: Searches
) -> None:
    fake = Claude(
        plan={
            "query": "Subvocal glasses funding",
            "source": "twitter",
            "since": "2026-10-29",
            "until": "2026-10-28",
        }
    )
    monkeypatch.setattr(ask_module, "claude_json", fake.claude_json)
    history = [Turn("What did I save about wearables?", "Subvocal's glasses [item:x].")]
    chunks = await ask_module.retrieve(
        None,  # type: ignore[arg-type]
        "how much did they raise?",
        uuid.uuid4(),
        history=history,
        today=date(2026, 10, 30),
    )
    assert len(chunks) == 1
    planned = fake.planned[0]
    assert planned["model"] == get_settings().claude_analysis_model
    assert planned["effort"] == "low"
    assert "Today is 2026-10-30 (UTC)." in planned["messages"][0]["content"]
    # Citation markers mean nothing to a search.
    assert "[item:x]" not in planned["messages"][0]["content"]
    assert [(s["query"], s["filters"]) for s in searches] == [
        (
            "Subvocal glasses funding",
            # The dates came back the wrong way round.
            Filters(source="twitter", since=date(2026, 10, 28), until=date(2026, 10, 29)),
        ),
        ("Subvocal glasses funding", None),
    ]
    assert searches[0]["per_parent"] == ask_module.PER_SOURCE


@pytest.mark.parametrize(
    "plan",
    [
        RuntimeError("overloaded"),
        Refused("cyber"),
        ValueError("the answer was cut off at max_tokens"),
        {"query": "  ", "source": "tiktok", "since": "last week", "until": None},
    ],
)
async def test_without_a_usable_plan_the_question_is_searched_as_asked(
    monkeypatch: pytest.MonkeyPatch, searches: Searches, plan: Any
) -> None:
    fake = Claude(plan=plan)
    monkeypatch.setattr(ask_module, "claude_json", fake.claude_json)
    await ask_module.retrieve(None, "Zig allocators?", uuid.uuid4())  # type: ignore[arg-type]
    assert [(s["query"], s["filters"]) for s in searches] == [("Zig allocators?", None)]


# Scoped questions


def test_an_item_scope_searches_only_that_item(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    claude: Claude,
) -> None:
    auth, user_id = account
    asked = _item(db, user_id, chunks=4)
    other = _item(db, user_id, chunks=2)
    _page(db, user_id, "spices", [asked, other])

    found = _search(client, user_id, scope=Scope(item_ids=(asked,)))
    assert {c.parent_id for c in found} == {asked}
    assert len(found) == 4
    # Unscoped, the same search finds the rest.
    assert len({c.parent_id for c in _search(client, user_id)}) == 3

    resp = client.post("/v1/ask", json={"question": "spice?", "item": str(asked)}, headers=auth)
    assert resp.status_code == 200
    assert _cited(claude.context) == {str(asked)}
    # One item's passages aren't capped: all four fit.
    assert claude.context.count(f"[item:{asked}]") == 4


def test_a_topic_scope_searches_the_page_and_its_sources(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    claude: Claude,
) -> None:
    auth, user_id = account
    cited = _item(db, user_id)
    _item(db, user_id)
    _page(db, user_id, "spices", [cited])
    _page(db, user_id, "trade-routes", [])

    resp = client.post(
        "/v1/ask/stream", json={"question": "spice?", "topic": "spices"}, headers=auth
    )
    assert resp.status_code == 200
    assert _cited(claude.context) == {str(cited), "spices"}


def test_filters_keep_items_by_source_and_save_day_and_leave_pages_out(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any, voyage: None
) -> None:
    _, user_id = account
    tweet = _item(db, user_id, source="twitter", created_at=datetime(2026, 10, 29, 23, tzinfo=UTC))
    article = _item(db, user_id, source="web", created_at=datetime(2026, 10, 30, 1, tzinfo=UTC))
    _page(db, user_id, "spices", [tweet, article])

    def parents(filters: Filters) -> set[uuid.UUID]:
        return {c.parent_id for c in _search(client, user_id, filters=filters)}

    assert parents(Filters(source="twitter")) == {tweet}
    assert parents(Filters(since=date(2026, 10, 30), until=date(2026, 10, 30))) == {article}
    assert parents(Filters(until=date(2026, 10, 29))) == {tweet}
    assert parents(Filters(source="youtube")) == set()
    # Days are the user's: in New York both were saved on the evening of the 29th.
    new_york = ZoneInfo("America/New_York")
    october_29 = Filters(since=date(2026, 10, 29), until=date(2026, 10, 29), tz=new_york)
    assert parents(october_29) == {tweet, article}


def test_today_and_save_days_are_the_apps_time_zone(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    claude: Claude,
) -> None:
    assert ask_module.zone("America/New_York") == ZoneInfo("America/New_York")
    assert ask_module.zone("Mars/Olympus_Mons") is UTC
    assert ask_module.zone("../../etc/passwd") is UTC
    assert ask_module.zone(None) is UTC

    auth, user_id = account
    _item(db, user_id, created_at=datetime(2026, 10, 30, 2, tzinfo=UTC))
    body = {"question": "What did I save yesterday?", "zone": "America/New_York"}
    assert client.post("/v1/ask", json=body, headers=auth).status_code == 200
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    assert f"Today is {today} (America/New_York)." in claude.planned[-1]["messages"][0]["content"]
    assert claude.context.startswith(f"Today is {today} (America/New_York).")
    # 02:00 UTC on the 30th is the evening of the 29th in New York.
    assert "saved 2026-10-29" in claude.context


def test_a_long_chat_sends_each_turn_cut_short(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    claude: Claude,
) -> None:
    auth, user_id = account
    _item(db, user_id)
    turn = {"question": "word " * 3_000, "answer": "word " * 20_000}
    body = {"question": "And then?", "history": [turn]}
    assert client.post("/v1/ask", json=body, headers=auth).status_code == 200
    earlier = claude.asked[-1]["messages"][:2]
    assert [len(m["content"]) for m in earlier] == [ask_module.TURN_CHARS] * 2
    assert len(claude.planned[-1]["messages"][0]["content"]) < 3 * ask_module.TURN_CHARS


def test_asking_about_someone_elses_item_or_page_is_a_404(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    claude: Claude,
) -> None:
    auth, _ = account
    other = signup(client)
    try:
        other_id = uuid.UUID(client.get("/auth/me", headers=other).json()["id"])
        theirs = _item(db, other_id)
        _page(db, other_id, "their-page", [theirs])
        for path in ("/v1/ask", "/v1/ask/stream"):
            for scope in ({"item": str(theirs)}, {"topic": "their-page"}, {"topic": "nope"}):
                resp = client.post(path, json={"question": "spice?", **scope}, headers=auth)
                assert resp.status_code == 404, (path, scope)
        assert claude.asked == [] and claude.planned == []
    finally:
        client.delete("/auth/me", headers=other)


def test_an_item_and_a_topic_together_are_a_422(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID]
) -> None:
    auth, _ = account
    body = {"question": "spice?", "item": str(uuid.uuid4()), "topic": "spices"}
    for path in ("/v1/ask", "/v1/ask/stream"):
        assert client.post(path, json=body, headers=auth).status_code == 422


# What the answer sees


def _chunk(parent: uuid.UUID, n: int, title: str = "A title") -> Candidate:
    return Candidate(
        kind="item",
        chunk_id=uuid.uuid4(),
        parent_id=parent,
        ord=n,
        text=f"chunk {n}",
        title=title,
        url=None,
        slug=None,
    )


async def test_context_holds_at_most_two_chunks_of_one_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long, short, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    chunks = [_chunk(long, n) for n in range(5)] + [_chunk(short, 0), _chunk(other, 0)]
    reranked: list[tuple[list[str], int | None]] = []

    async def embed(query: str) -> list[float]:
        return [0.0]

    async def found(*_: object) -> list[Candidate]:
        return chunks

    async def rerank(
        query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        reranked.append((documents, top_k))
        return [(i, 1 - i / 100) for i in range(len(documents))][:top_k]

    monkeypatch.setattr(retrieval, "embed_query", embed)
    monkeypatch.setattr(retrieval, "_dense_search", found)
    monkeypatch.setattr(retrieval, "_lexical_search", found)
    monkeypatch.setattr(retrieval, "rerank", rerank)

    class Session:
        async def commit(self) -> None:
            pass

    hits = await hybrid_search(Session(), "q", uuid.uuid4(), final_limit=6, per_parent=2)  # type: ignore[arg-type]
    assert [h.parent_id for h in hits] == [long, long, short, other]
    # The reranker reads each chunk under its title, and scores them all so
    # the ones past the cap can fill in.
    documents, top_k = reranked[0]
    assert documents[0] == "A title\n\nchunk 0"
    assert top_k == len(chunks)

    # /search keeps every chunk of one item.
    hits = await hybrid_search(Session(), "q", uuid.uuid4(), final_limit=6)  # type: ignore[arg-type]
    assert [h.parent_id for h in hits] == [long] * 5 + [short]


def test_each_context_chunk_says_its_source_and_day() -> None:
    item = _chunk(uuid.uuid4(), 0)
    item.source = "twitter"
    item.at = datetime(2026, 10, 29, 23, 30, tzinfo=UTC)
    page = Candidate(
        kind="topic_page",
        chunk_id=uuid.uuid4(),
        parent_id=uuid.uuid4(),
        ord=0,
        text="A page.",
        title="Spices",
        url=None,
        slug="spices",
        at=datetime(2026, 10, 30, 8, tzinfo=UTC),
    )
    context = ask_module._format_context([item, page], UTC)
    assert f"[item:{item.parent_id}] A title\nA post on X (Twitter), saved 2026-10-29\n" in context
    assert "[wiki:spices] Spices\nA wiki page, updated 2026-10-30\nA page." in context
