import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from arq import Retry
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, insert, select
from voyageai.error import RateLimitError

from app.ai import synthesize as synthesize_ai
from app.ai.synthesize import (
    WIKILINK_RE,
    PatchedPage,
    ProposedSlugs,
    _normalize_slug,
    forget_in_body,
    patch_page,
    propose_slugs,
    remove_from_page,
)
from app.config import get_settings
from app.db.models import EMBED_DIM, Chat, Item, TopicPage, TopicPageChunk
from app.errors import Refused
from app.main import app
from app.workers import synthesize as synthesize_worker
from app.workers.synthesize import forget_item, synthesize_topics
from tests.conftest import Jobs, signup

X = uuid.UUID("11111111-1111-1111-1111-111111111111")
Y = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_normalize_slug() -> None:
    assert _normalize_slug("Rust Async Runtimes!") == "rust-async-runtimes"
    assert _normalize_slug("  LLM Wiki  ") == "llm-wiki"


def test_wikilink_regex() -> None:
    matches = WIKILINK_RE.findall("see [[rust-async]] and [[llm-wiki]] for more")
    assert matches == ["rust-async", "llm-wiki"]


def test_wiki_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/wiki").status_code == 401
        assert client.get("/wiki/anything").status_code == 401


def test_forget_in_body_drops_the_items_claims_and_links_to_deleted_pages() -> None:
    body = "\n".join(
        [
            "# Memory",
            "",
            f"Shared intro [item:{Y}].",
            "",
            "## Only X",
            "",
            f"- First X fact [item:{X}]",
            f"- Second X fact [item:{str(X).upper()}].",
            "",
            "## Mixed",
            "",
            f"X says one thing [item:{X}]. Y says another [item:{Y}]. "
            f"Both agree [item:{X}] [item:{Y}].",
            "",
            "---",
            "",
            "## Related",
            "",
            "- [[gone-topic]]",
            "- [[kept-topic]]",
            "See [[gone-topic]] for more.",
        ]
    )
    assert forget_in_body(body, X, {"gone-topic"}) == "\n".join(
        [
            "# Memory",
            "",
            f"Shared intro [item:{Y}].",
            "",
            "## Mixed",
            "",
            f"Y says another [item:{Y}]. Both agree [item:{Y}].",
            "",
            "---",
            "",
            "## Related",
            "",
            "- [[kept-topic]]",
            "See Gone Topic for more.",
        ]
    )


def test_forget_in_body_leaves_a_page_without_the_item_alone() -> None:
    body = f"## Notes\n\n- One [item:{Y}]\n- Two, see [[other]]\n\n---\n\n1. Three [item:{Y}]."
    assert forget_in_body(body, X, set()) == body


# Deleting an item, end to end against Postgres. Claude and Voyage are stubbed.


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


@pytest.fixture
def embed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def embed_page_chunks(body: str, title: str) -> list[tuple[str, list[float]]]:
        return [(body, [0.1] * EMBED_DIM)]

    monkeypatch.setattr(synthesize_worker, "embed_page_chunks", embed_page_chunks)


def _turn(answer: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "question": "What did I save?",
        "answer": answer,
        "citations": citations,
        "status": "done",
        "error": None,
    }


def test_deleting_an_item_takes_it_out_of_the_wiki_and_ask_history(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    jobs: Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    x, y = uuid.uuid4(), uuid.uuid4()
    pages = {
        # Only x supported it, so it goes, and so do links to it.
        "only-x": ([x], f"Only x [item:{x}]. See [[shared]]."),
        # Both did: x's claims and links go, y's stay.
        "shared": (
            [x, y],
            f"## Overview\n\nAbout y [item:{y}].\n\n## From x\n\nX claim [item:{x}]. "
            f"Joint claim [item:{x}] [item:{y}].\n\n## Related\n\n- [[only-x]]\n- [[x-stub]]",
        ),
        # A stub x's page linked to; once nothing links to it, it goes.
        "x-stub": (None, "_pending_"),
        # Only links to a page x supported.
        "linker": ([y], f"Mentions [[only-x]] in passing [item:{y}]."),
        "untouched": ([y], f"Y only [item:{y}]."),
    }
    x_chat, y_chat = uuid.uuid4(), uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__),
            [
                {
                    "id": i,
                    "user_id": user_id,
                    "url": f"https://example.com/{i}",
                    "source": "webpage",
                    "status": "ready",
                    "title": f"Item {i}",
                }
                for i in (x, y)
            ],
        )
        conn.execute(
            insert(TopicPage.__table__),
            [
                {
                    "user_id": user_id,
                    "slug": slug,
                    "title": slug,
                    "body_md": body,
                    "backlinks": sorted(set(WIKILINK_RE.findall(body))) or None,
                    "source_item_ids": sources,
                    "version": 1,
                }
                for slug, (sources, body) in pages.items()
            ],
        )
        x_citations = [
            {"kind": "item", "id": str(x), "title": "Item x", "url": None, "slug": None},
            {"kind": "item", "id": str(y), "title": "Item y", "url": None, "slug": None},
            {
                "kind": "topic_page",
                "id": str(uuid.uuid4()),
                "title": "Only x",
                "url": None,
                "slug": "only-x",
            },
        ]
        y_turn = _turn(f"Just y [item:{y}].", x_citations[1:2])
        conn.execute(
            insert(Chat.__table__),
            [
                {
                    "id": x_chat,
                    "user_id": user_id,
                    "turns": [
                        _turn(
                            f"From x [item:{x}] and y [item:{y}], see [wiki:only-x].", x_citations
                        )
                    ],
                },
                {"id": y_chat, "user_id": user_id, "turns": [y_turn]},
            ],
        )

    asked: list[tuple[str, list[str]]] = []

    async def remove_from_page(*, body: str, item_id: uuid.UUID, gone_slugs: list[str]) -> str:
        assert item_id == x
        asked.append((body.split("\n")[0], gone_slugs))
        # Claude drops the "see also" link that only x's material needed.
        return body.replace("\n- [[x-stub]]", "")

    monkeypatch.setattr(synthesize_worker, "remove_from_page", remove_from_page)

    assert client.delete(f"/items/{x}", headers=auth).status_code == 204
    assert jobs == [("forget_item", str(user_id), str(x), {"_job_id": f"forget:{x}"})]
    result = client.portal.call(forget_item, {}, str(user_id), str(x))  # type: ignore[union-attr]
    assert result == "removed:1,rewrote:2,stubs:1"
    assert sorted(asked) == [
        ("## Overview", ["only-x"]),
        (f"Mentions [[only-x]] in passing [item:{y}].", ["only-x"]),
    ]

    wiki = {p["slug"]: p for p in client.get("/wiki", headers=auth).json()}
    assert set(wiki) == {"shared", "linker", "untouched"}
    shared = client.get("/wiki/shared", headers=auth).json()
    assert shared["body_md"] == (
        f"## Overview\n\nAbout y [item:{y}].\n\n## From x\n\nJoint claim [item:{y}]."
    )
    assert shared["source_item_ids"] == [str(y)]
    assert shared["backlinks"] is None
    assert shared["version"] == 2
    linker = client.get("/wiki/linker", headers=auth).json()
    assert linker["body_md"] == f"Mentions Only X in passing [item:{y}]."
    assert linker["version"] == 2
    assert wiki["untouched"]["version"] == 1

    with db.connect() as conn:
        chunks = (
            conn.execute(select(TopicPageChunk.text).where(TopicPageChunk.user_id == user_id))
            .scalars()
            .all()
        )
    assert chunks and not any(str(x) in text or "only-x" in text for text in chunks)

    turn = client.get(f"/chats/{x_chat}", headers=auth).json()["turns"][0]
    assert turn["answer"] == f"From x and y [item:{y}], see."
    assert [c["id"] for c in turn["citations"]] == [str(y)]
    assert client.get(f"/chats/{y_chat}", headers=auth).json()["turns"] == [y_turn]

    # Running it again finds nothing left to do.
    again = client.portal.call(forget_item, {}, str(user_id), str(x))  # type: ignore[union-attr]
    assert again == "removed:0,rewrote:0,stubs:0"


def test_synthesis_keeps_nothing_of_an_item_deleted_while_claude_writes(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                source="webpage",
                status="ready",
                title="Soon deleted",
            )
        )
        conn.execute(
            insert(TopicPage.__table__).values(
                user_id=user_id,
                slug="soon-deleted",
                title="Soon Deleted",
                body_md="Before.",
                source_item_ids=[uuid.uuid4()],
                version=1,
            )
        )

    async def propose_slugs(**_: Any) -> ProposedSlugs:
        return ProposedSlugs(slugs=["soon-deleted"])

    async def patch_page(**kwargs: Any) -> PatchedPage:
        with db.begin() as conn:
            conn.execute(delete(Item.__table__).where(Item.id == item_id))
        return PatchedPage(
            slug=kwargs["slug"],
            title="Soon Deleted",
            body_md=f"A claim [item:{item_id}]",
            backlinks=[],
        )

    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", patch_page)

    result = client.portal.call(synthesize_topics, {}, str(item_id))  # type: ignore[union-attr]
    assert result == "deleted"
    page = client.get("/wiki/soon-deleted", headers=auth).json()
    assert (page["body_md"], page["version"]) == ("Before.", 1)


def test_forget_waits_until_the_item_is_deleted(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any
) -> None:
    _, user_id = account
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                source="webpage",
                status="ready",
            )
        )
    with pytest.raises(Retry):
        client.portal.call(forget_item, {}, str(user_id), str(item_id))  # type: ignore[union-attr]


def test_forget_retries_after_a_rate_limit_and_keeps_what_it_finished(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    x, y = uuid.uuid4(), uuid.uuid4()
    shared = f"Y fact [item:{y}]. X fact [item:{x}]."
    with db.begin() as conn:
        conn.execute(
            insert(TopicPage.__table__),
            [
                {
                    "user_id": user_id,
                    "slug": slug,
                    "title": slug,
                    "body_md": body,
                    "source_item_ids": sources,
                    "version": 1,
                }
                for slug, sources, body in (
                    ("only-x", [x], f"Only x [item:{x}]."),
                    ("shared", [x, y], shared),
                )
            ],
        )

    async def remove_from_page(**_: Any) -> None:
        return None

    async def embed_page_chunks(body: str, title: str) -> list[tuple[str, list[float]]]:
        raise RateLimitError("slow down")

    monkeypatch.setattr(synthesize_worker, "remove_from_page", remove_from_page)
    monkeypatch.setattr(synthesize_worker, "embed_page_chunks", embed_page_chunks)

    with pytest.raises(Retry):
        client.portal.call(forget_item, {}, str(user_id), str(x))  # type: ignore[union-attr]
    # The page only x supported is already gone; the rewrite waits for the retry.
    assert [p["slug"] for p in client.get("/wiki", headers=auth).json()] == ["shared"]
    page = client.get("/wiki/shared", headers=auth).json()
    assert page["body_md"] == shared
    assert page["source_item_ids"] == [str(y)]


# Held topics: a new page waits until two items support it.


def _ready_item(db: Any, user_id: uuid.UUID, **values: Any) -> uuid.UUID:
    item_id = uuid.uuid4()
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=f"https://example.com/{item_id}",
                source="webpage",
                status="ready",
                wiki_status="pending",
                **values,
            )
        )
    return item_id


def _held(db: Any, item_id: uuid.UUID) -> tuple[list[str] | None, str | None]:
    with db.connect() as conn:
        row = conn.execute(
            select(Item.held_topics, Item.wiki_status).where(Item.id == item_id)
        ).one()
    return row.held_topics, row.wiki_status


def _patch_appending(calls: list[tuple[str, uuid.UUID, str]]) -> Any:
    async def patch_page(**kwargs: Any) -> PatchedPage:
        calls.append((kwargs["existing_body"], kwargs["item_id"], kwargs["item_title"]))
        body = f"{kwargs['existing_body']} Claim [item:{kwargs['item_id']}].".strip()
        return PatchedPage(slug=kwargs["slug"], title="Sourdough", body_md=body, backlinks=[])

    return patch_page


def test_a_new_topic_one_item_proposes_is_held_and_gets_no_page(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    item_id = _ready_item(db, user_id, title="Sourdough notes")

    async def propose_slugs(**kwargs: Any) -> ProposedSlugs:
        assert kwargs["held_slugs"] == []
        return ProposedSlugs(slugs=["sourdough"])

    calls: list[tuple[str, uuid.UUID, str]] = []
    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", _patch_appending(calls))

    result = client.portal.call(synthesize_topics, {}, str(item_id))  # type: ignore[union-attr]
    assert result == "held:sourdough"
    assert calls == []
    assert client.get("/wiki", headers=auth).json() == []
    assert _held(db, item_id) == (["sourdough"], "done")


def test_a_link_stub_doesnt_become_a_page_for_one_item(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    item_id = _ready_item(db, user_id, title="Vector search notes")
    with db.begin() as conn:
        conn.execute(
            insert(TopicPage.__table__).values(
                user_id=user_id,
                slug="vector-databases",
                title="Vector Databases",
                body_md="_pending_",
                version=1,
            )
        )

    async def propose_slugs(**kwargs: Any) -> ProposedSlugs:
        # A stub is listed with the pages, which the proposer prefers.
        assert "vector-databases" in kwargs["existing_slugs"]
        return ProposedSlugs(slugs=["vector-databases"])

    calls: list[tuple[str, uuid.UUID, str]] = []
    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", _patch_appending(calls))

    result = client.portal.call(synthesize_topics, {}, str(item_id))  # type: ignore[union-attr]
    assert result == "held:vector-databases"
    assert calls == []
    page = client.get("/wiki/vector-databases", headers=auth).json()
    assert (page["body_md"], page["source_item_ids"]) == ("_pending_", None)
    assert _held(db, item_id) == (["vector-databases"], "done")


def test_a_second_item_on_a_held_topic_writes_the_page_from_both(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    first = _ready_item(db, user_id, title="First", held_topics=["sourdough", "rye"])
    second = _ready_item(db, user_id, title="Second")
    asked: dict[str, Any] = {}

    async def propose_slugs(**kwargs: Any) -> ProposedSlugs:
        asked.update(kwargs)
        return ProposedSlugs(slugs=["sourdough"])

    calls: list[tuple[str, uuid.UUID, str]] = []
    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", _patch_appending(calls))

    result = client.portal.call(synthesize_topics, {}, str(second))  # type: ignore[union-attr]
    assert result == "ok:sourdough"
    # The proposer is offered what earlier items wait on, so it can match it.
    assert asked["held_slugs"] == ["rye", "sourdough"]
    # The earlier item goes in first, then this one on top of it.
    assert calls == [("", first, "First"), (f"Claim [item:{first}].", second, "Second")]
    page = client.get("/wiki/sourdough", headers=auth).json()
    assert page["body_md"] == f"Claim [item:{first}]. Claim [item:{second}]."
    assert page["source_item_ids"] == [str(first), str(second)]
    assert page["version"] == 1
    assert _held(db, first) == (["rye"], "pending")
    assert _held(db, second) == (None, "done")


def test_a_hold_that_goes_while_claude_drafts_leaves_the_topic_held(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    embed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth, user_id = account
    first = _ready_item(db, user_id, title="First", held_topics=["sourdough"])
    second = _ready_item(db, user_id, title="Second")

    async def propose_slugs(**_: Any) -> ProposedSlugs:
        return ProposedSlugs(slugs=["sourdough"])

    calls: list[tuple[str, uuid.UUID, str]] = []
    patch = _patch_appending(calls)

    async def patch_page(**kwargs: Any) -> PatchedPage:
        # The earlier item is deleted while Claude writes the page from it.
        with db.begin() as conn:
            conn.execute(delete(Item.__table__).where(Item.id == first))
        return await patch(**kwargs)

    monkeypatch.setattr(synthesize_worker, "propose_slugs", propose_slugs)
    monkeypatch.setattr(synthesize_worker, "patch_page", patch_page)

    result = client.portal.call(synthesize_topics, {}, str(second))  # type: ignore[union-attr]
    assert result == "held:sourdough"
    assert client.get("/wiki", headers=auth).json() == []
    assert _held(db, second) == (["sourdough"], "done")


async def test_the_proposer_sees_held_topics_and_brings_at_most_two_new_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: dict[str, Any] = {}

    async def claude_json(**kwargs: Any) -> dict[str, Any]:
        asked.update(kwargs)
        return {"slugs": ["brand-new-one", "Brand New Two", "brand-new-three", "Baking"]}

    monkeypatch.setattr(synthesize_ai, "claude_json", claude_json)
    proposed = await propose_slugs(
        item_title="Bread",
        item_summary=None,
        item_topics=[],
        item_tags=[],
        existing_slugs=["baking"],
        held_slugs=["sourdough"],
    )
    assert proposed.slugs == ["brand-new-one", "brand-new-two", "baking"]
    facts = asked["messages"][0]["content"]
    assert "Existing pages: baking\n" in facts
    assert "Waiting topics: sourdough" in facts
    assert asked["effort"] == "low"
    assert asked["schema"]["additionalProperties"] is False


async def test_patch_page_puts_the_item_first_under_a_cache_breakpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def claude(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=f"New [item:{X}], see [[other]]."),
            ],
        )

    monkeypatch.setattr(synthesize_ai, "claude", claude)
    for slug, body in (("one", "First page."), ("two", "Second page.")):
        page = await patch_page(
            slug=slug,
            existing_body=body,
            existing_title=None,
            item_id=X,
            item_title="Item",
            item_summary="Summary",
            item_text="Text",
            related_slugs=["one", "two", "other"],
        )
    assert page == PatchedPage("two", "Two", f"New [item:{X}], see [[other]].", ["other"])

    item_blocks = [c["messages"][0]["content"][0] for c in calls]
    page_blocks = [c["messages"][0]["content"][1] for c in calls]
    # Byte for byte the same across the item's pages, so the second reads the cache.
    assert item_blocks[0] == item_blocks[1]
    assert item_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert str(X) in item_blocks[0]["text"] and "other" in item_blocks[0]["text"]
    assert "Page slug: one" in page_blocks[0]["text"] and "First page." in page_blocks[0]["text"]
    assert "Page slug: two" in page_blocks[1]["text"] and "cache_control" not in page_blocks[1]
    assert all(c["effort"] == "medium" and isinstance(c["system"], str) for c in calls)


async def test_a_refused_rewrite_leaves_forgetting_to_forget_in_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def claude(**_: Any) -> SimpleNamespace:
        raise Refused("cyber")

    monkeypatch.setattr(synthesize_ai, "claude", claude)
    assert await remove_from_page(body=f"A [item:{X}].", item_id=X, gone_slugs=[]) is None
