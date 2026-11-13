"""Ask's lookups: the tools Claude calls while it answers, and the loop that
runs them. Claude and Voyage are stubbed; the lookups read Postgres."""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from app.ai import ask as ask_module
from app.ai import retrieval
from app.ai.ask_tools import MAX_CALLS, TOOLS, Toolbox, echo
from app.ai.retrieval import Scope
from app.config import get_settings
from app.db.models import EMBED_DIM, Item, ItemChunk, TopicPage
from app.db.session import get_sessionmaker
from app.main import app
from tests.conftest import signup

VEC = [0.1] * EMBED_DIM


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
def other(client: TestClient) -> Iterator[uuid.UUID]:
    headers = signup(client)
    yield uuid.UUID(client.get("/auth/me", headers=headers).json()["id"])
    client.delete("/auth/me", headers=headers)


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def voyage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def embed(query: str) -> list[float]:
        return VEC

    async def rerank(
        query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        return [(i, 1 - i / 100) for i in range(len(documents))][:top_k]

    monkeypatch.setattr(retrieval, "embed_query", embed)
    monkeypatch.setattr(retrieval, "rerank", rerank)


def _item(db: Any, user_id: uuid.UUID, title: str, **values: Any) -> uuid.UUID:
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                title=title,
                **{"source": "web", "status": "ready", **values},
            )
        )
        conn.execute(
            insert(ItemChunk.__table__).values(
                item_id=item_id, user_id=user_id, ord=0, text=f"ramen {title}", embedding=VEC
            )
        )
    return item_id


def _call(name: str, **args: Any) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use", id=f"toolu_{uuid.uuid4().hex[:8]}", name=name, input=args
    )


class Claude:
    """Answers each turn with the next of `turns`: a string is the answer, a
    list is the lookups it asks for. The last one repeats."""

    def __init__(self, *turns: str | list[SimpleNamespace]) -> None:
        self.turns = list(turns)
        self.asked: list[dict[str, Any]] = []

    async def claude_json(self, **params: Any) -> dict[str, Any]:
        return {"query": "ramen", "source": None, "since": None, "until": None}

    @asynccontextmanager
    async def claude_stream(self, **params: Any) -> AsyncIterator[Any]:
        # The loop appends to the list it sent: keep what this turn saw.
        self.asked.append({**params, "messages": list(params["messages"])})
        turn = self.turns[min(len(self.asked), len(self.turns)) - 1]

        async def text() -> AsyncIterator[str]:
            if isinstance(turn, str):
                yield turn

        async def final() -> Any:
            if isinstance(turn, str):
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=turn)], stop_reason="end_turn"
                )
            thinking = SimpleNamespace(type="thinking", thinking="", signature="sig")
            return SimpleNamespace(content=[thinking, *turn], stop_reason="tool_use")

        yield SimpleNamespace(text_stream=text(), get_final_message=final)


def _use(monkeypatch: pytest.MonkeyPatch, fake: Claude) -> Claude:
    monkeypatch.setattr(ask_module, "claude_json", fake.claude_json)
    monkeypatch.setattr(ask_module, "claude_stream", fake.claude_stream)
    return fake


def _run(
    client: TestClient, user_id: uuid.UUID, name: str, scope: Scope | None = None, **args: Any
) -> str:
    async def run() -> str:
        async with get_sessionmaker()() as session:
            return await Toolbox(session, user_id, scope, UTC).run(name, args)

    return client.portal.call(run)  # type: ignore[union-attr]


NO_FILTERS: dict[str, Any] = {"source": None, "kind": None, "since": None, "until": None}
LIST_ALL: dict[str, Any] = {**NO_FILTERS, "tag": None, "done": None, "offset": 0}


def test_every_tool_is_strict_and_asks_for_all_its_fields() -> None:
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])


def test_a_question_about_many_items_is_answered_from_a_list(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    other: uuid.UUID,
    db: Any,
    voyage: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    ramen = _item(db, user_id, "Ichiran", kind="place", summary="Solo ramen booths.")
    done = _item(db, user_id, "Katz's", kind="place", done_at=datetime.now(UTC))
    _item(db, user_id, "A ramen recipe", kind="recipe")
    _item(db, other, "Someone else's place", kind="place")
    fake = _use(
        monkeypatch,
        Claude(
            [_call("list_items", **{**LIST_ALL, "kind": "place"})],
            f"Two places: Ichiran [item:{ramen}] and Katz's [item:{done}].",
        ),
    )

    resp = client.post(
        "/v1/ask/stream", json={"question": "Which places did I save?"}, headers=auth
    )
    assert resp.status_code == 200
    events = [line.split(": ", 1)[1] for line in resp.text.splitlines() if line.startswith("event")]
    assert events == ["step", "token", "done"]
    assert '"text": "Listing your places"' in resp.text
    # Both cited items come back as source cards.
    assert str(ramen) in resp.text.split("event: done")[1]
    assert str(done) in resp.text.split("event: done")[1]

    first, second = fake.asked
    assert first["tools"] == TOOLS
    # The turn that asked goes back as it came, thinking included, then the answer.
    assistant, results = second["messages"][-2:]
    assert [b.type for b in assistant["content"]] == ["thinking", "tool_use"]
    (result,) = results["content"]
    assert result["tool_use_id"] == assistant["content"][1].id
    listed = result["content"]
    assert listed.startswith("2 saved items match; these are 1 to 2.")
    assert f"[item:{ramen}] Ichiran (place, a web page, saved " in listed
    assert "  Solo ramen booths." in listed
    assert "marked done" in listed.split("Katz's")[1]
    assert "recipe" not in listed and "Someone else" not in listed


def test_lookups_stop_after_the_last_round(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    _item(db, user_id, "Ichiran")
    # It never answers: every turn asks for another search, and one for a
    # tool that doesn't exist.
    fake = _use(monkeypatch, Claude([_call("search", query="ramen", **NO_FILTERS), _call("fly")]))

    resp = client.post("/v1/ask", json={"question": "ramen?"}, headers=auth)
    assert resp.status_code == 200
    assert resp.json()["answer"].startswith("I couldn't finish looking that up.")
    # Nothing was answered, so the passages it started from aren't its sources.
    assert resp.json()["citations"] == []
    assert len(fake.asked) == ask_module.MAX_LOOKUP_ROUNDS + 1
    found, unknown, note = fake.asked[-1]["messages"][-1]["content"]
    assert "Ichiran" in found["content"] and "is_error" not in found
    assert unknown["is_error"] is True
    assert note == {
        "type": "text",
        "text": "That was the last lookup. Answer now from what you have.",
    }
    # Told once, after the last round's answers.
    assert all(len(turn["messages"][-1]["content"]) == 2 for turn in fake.asked[1:-1])


def test_a_turn_gets_only_so_many_lookups_and_an_answer_for_every_call(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    _item(db, user_id, "Ichiran")
    calls = [_call("list_items", **{**LIST_ALL, "kind": "howto"}) for _ in range(MAX_CALLS + 2)]
    fly = _call("fly")
    fake = _use(monkeypatch, Claude([*calls, fly], "Done."))

    resp = client.post("/v1/ask/stream", json={"question": "ramen?"}, headers=auth)
    # One step per lookup that ran, named for people.
    assert resp.text.count("event: step") == MAX_CALLS
    assert resp.text.count('"text": "Listing your how-tos"') == MAX_CALLS
    results = fake.asked[1]["messages"][-1]["content"]
    # The ones over the limit weren't run, but each is told so.
    assert [r["tool_use_id"] for r in results] == [c.id for c in [*calls, fly]]
    assert ["is_error" in r for r in results] == [False] * MAX_CALLS + [True] * 3
    assert results[-1]["content"] == f"Only {MAX_CALLS} lookups at a time. Ask again for this one."


def test_after_a_fallback_only_the_calls_the_turn_keeps_are_run(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    _item(db, user_id, "Ichiran")
    declined = _call("search", query="from the model that declined", **NO_FILTERS)
    kept = _call("list_items", **LIST_ALL)
    fake = _use(monkeypatch, Claude([declined, SimpleNamespace(type="fallback"), kept], "Ichiran."))

    resp = client.post("/v1/ask/stream", json={"question": "ramen?"}, headers=auth)
    assert resp.text.count("event: step") == 1 and "Listing what you saved" in resp.text
    assistant, results = fake.asked[1]["messages"][-2:]
    # The declined model's thinking and call are gone, and nothing answers them.
    assert [b.type for b in assistant["content"]] == ["fallback", "tool_use"]
    assert [r["tool_use_id"] for r in results["content"]] == [kept.id]


def test_an_ask_about_one_item_searches_all_of_its_passages(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    voyage: None,
) -> None:
    _, user_id = account
    long = _item(db, user_id, "A long transcript")
    with db.begin() as conn:
        conn.execute(
            insert(ItemChunk.__table__),
            [
                {"item_id": long, "user_id": user_id, "ord": n, "text": f"ramen part {n}"}
                | {"embedding": VEC}
                for n in range(1, 5)
            ],
        )
    found = _run(client, user_id, "search", Scope(item_ids=(long,)), query="ramen", **NO_FILTERS)
    assert found.count(f"[item:{long}]") == 5
    # Across the library, one item gets two passages at most.
    assert _run(client, user_id, "search", query="ramen", **NO_FILTERS).count("[item:") == 2


def test_lookups_read_only_the_users_library_and_the_scope(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    other: uuid.UUID,
    db: Any,
    voyage: None,
) -> None:
    _, user_id = account
    mine = _item(
        db,
        user_id,
        "Ichiran",
        kind="place",
        share_text="Go on a weekday",
        raw_text="Body:\nTonkotsu only.",
        details={"places": [{"name": "Ichiran", "address": "Shibuya, Tokyo"}]},
    )
    second = _item(db, user_id, "Second")
    theirs = _item(db, other, "Theirs")
    with db.begin() as conn:
        conn.execute(
            insert(TopicPage.__table__).values(
                user_id=user_id, slug="ramen", title="Ramen", body_md=f"Tonkotsu [item:{mine}]."
            )
        )

    read = _run(client, user_id, "read", ref=f"item:{mine}")
    assert "Place: Ichiran, Shibuya, Tokyo" in read
    assert "The user's note: Go on a weekday" in read
    assert read.endswith("Body:\nTonkotsu only.")
    assert _run(client, user_id, "read", ref="wiki:ramen").startswith("[wiki:ramen] Ramen\n")
    for ref in (f"item:{theirs}", "wiki:nothing", "item:not-an-id", "page:ramen"):
        with pytest.raises(LookupError):
            _run(client, user_id, "read", ref=ref)

    assert "Theirs" not in _run(client, user_id, "search", query="ramen", **NO_FILTERS)
    assert _run(client, user_id, "list_items", **LIST_ALL).startswith("2 saved items match")
    # A day in the past holds none of them; a kind holds one.
    nothing = _run(client, user_id, "list_items", **{**LIST_ALL, "until": "2020-01-01"})
    assert nothing == "0 saved items match; none from 1 on."
    assert "1 saved items" in _run(client, user_id, "list_items", **{**LIST_ALL, "kind": "place"})

    # An Ask about one item sees nothing else.
    scope = Scope(item_ids=(second,))
    assert _run(client, user_id, "list_items", scope, **LIST_ALL).startswith("1 saved items match")
    assert "Ichiran" not in _run(client, user_id, "search", scope, query="ramen", **NO_FILTERS)
    for ref in (f"item:{mine}", "wiki:ramen"):
        with pytest.raises(LookupError):
            _run(client, user_id, "read", scope, ref=ref)


def test_a_turn_a_fallback_model_took_over_goes_back_without_what_came_before_it() -> None:
    blocks = [
        SimpleNamespace(type=kind)
        for kind in ("thinking", "text", "tool_use", "fallback", "thinking", "tool_use")
    ]
    assert [b.type for b in echo(blocks)] == ["text", "fallback", "thinking", "tool_use"]
    # Without one, the turn goes back whole.
    assert echo(blocks[:3]) == blocks[:3]
