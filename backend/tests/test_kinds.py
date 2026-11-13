"""Kinds: what a save is for, its details, the done mark and the places list."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from app.ai import analyze
from app.ai.analyze import read_kind, schema
from app.config import get_settings
from app.db.models import Item
from app.errors import Refused
from app.main import app
from app.workers import classify as classify_worker
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


def _item(db: Any, user_id: uuid.UUID, title: str, **values: Any) -> str:
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
    return str(item_id)


def test_an_analysis_keeps_only_its_kinds_details() -> None:
    filled = {
        "kind": "place",
        "places": [
            {"name": " Ichiran ", "address": "Shibuya, Tokyo"},
            {"name": "Katz's", "address": ""},
            {"name": "  ", "address": "nowhere"},
            # Named twice: kept once.
            {"name": "Ichiran", "address": "Shibuya, Tokyo "},
        ],
        "ingredients": ["noodles"],
        "steps": ["Queue."],
        "price": "$12",
    }
    assert read_kind(filled) == (
        "place",
        {
            "places": [
                {"name": "Ichiran", "address": "Shibuya, Tokyo"},
                {"name": "Katz's", "address": ""},
            ]
        },
    )
    assert read_kind({**filled, "kind": "recipe"}) == (
        "recipe",
        {"ingredients": ["noodles"], "steps": ["Queue."]},
    )
    assert read_kind({**filled, "kind": "product"}) == ("product", {"price": "$12"})
    assert read_kind({**filled, "kind": "media"}) == ("media", None)
    # A kind it doesn't know, or none, is "other".
    assert read_kind({"kind": "idea"}) == ("other", None)
    assert read_kind({}) == ("other", None)
    # Every analysis is asked for the kind and its details.
    assert {"kind", "places", "ingredients", "steps", "price"} <= set(schema()["required"])


def test_the_library_lists_by_kind_and_by_done(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any
) -> None:
    auth, user_id = account
    place = _item(db, user_id, "Ichiran", kind="place")
    recipe = _item(db, user_id, "Shoyu ramen", kind="recipe")
    _item(db, user_id, "From before kinds")

    def listed(**params: Any) -> list[str]:
        return [i["id"] for i in client.get("/v1/items", params=params, headers=auth).json()]

    assert listed(kind="place") == [place]
    assert len(listed()) == 3

    marked = client.put(f"/v1/items/{place}/done", json={"done": True}, headers=auth)
    assert marked.status_code == 200
    first = marked.json()["done_at"]
    assert first is not None
    # Marking it again keeps the first time; nothing is analysed again.
    again = client.put(f"/v1/items/{place}/done", json={"done": True}, headers=auth).json()
    assert again["done_at"] == first and again["status"] == "ready"
    assert listed(done="true") == [place]
    assert listed(kind="recipe", done="false") == [recipe]
    assert place not in listed(done="false")

    cleared = client.put(f"/v1/items/{place}/done", json={"done": False}, headers=auth)
    assert cleared.json()["done_at"] is None
    assert listed(done="true") == []


def test_the_map_gets_every_place_of_the_users_items_only(
    client: TestClient, account: tuple[dict[str, str], uuid.UUID], db: Any
) -> None:
    auth, user_id = account
    two = {"places": [{"name": "Ichiran", "address": "Tokyo"}, {"name": "Afuri", "address": ""}]}
    crawl = _item(db, user_id, "Ramen crawl", kind="place", details=two)
    _item(db, user_id, "A place with no name", kind="place")
    _item(db, user_id, "Not a place", kind="recipe", details={"steps": ["Boil."]})

    stranger = signup(client)
    try:
        stranger_id = uuid.UUID(client.get("/auth/me", headers=stranger).json()["id"])
        theirs = _item(
            db,
            stranger_id,
            "Theirs",
            kind="place",
            details={"places": [{"name": "X", "address": ""}]},
        )
        assert client.get("/v1/items/places", headers=auth).json() == [
            {
                "item": crawl,
                "title": "Ramen crawl",
                "name": "Ichiran",
                "address": "Tokyo",
                "done": False,
            },
            {"item": crawl, "title": "Ramen crawl", "name": "Afuri", "address": "", "done": False},
        ]
        # Someone else's item can't be marked, or seen.
        assert (
            client.put(f"/v1/items/{theirs}/done", json={"done": True}, headers=auth).status_code
            == 404
        )
    finally:
        client.delete("/auth/me", headers=stranger)
    assert client.get("/v1/items/places").status_code == 401

    detail = client.get(f"/v1/items/{crawl}", headers=auth).json()
    assert detail["kind"] == "place" and detail["details"] == two


def test_classify_fills_in_items_from_before_kinds(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user_id = account
    old = _item(db, user_id, "Ichiran", summary="Ramen in Tokyo.")
    declined = _item(db, user_id, "Declined")
    already = _item(db, user_id, "Shoyu ramen", kind="recipe")
    asked: list[dict[str, Any]] = []

    async def claude_json(**params: Any) -> dict[str, Any]:
        asked.append(params)
        if "Declined" in params["messages"][0]["content"]:
            raise Refused("bio")
        return {
            "kind": "place",
            "places": [{"name": "Ichiran", "address": "Tokyo"}],
            "ingredients": [],
            "steps": [],
            "price": "",
        }

    monkeypatch.setattr(analyze, "claude_json", claude_json)
    failed = client.portal.call(classify_worker.classify_all, user_id)  # type: ignore[union-attr]
    assert failed == 0
    # Only the items without a kind were read, from what is saved of them.
    assert len(asked) == 2
    assert "Summary: Ramen in Tokyo." in asked[-1]["messages"][0]["content"]
    assert set(asked[0]["schema"]["required"]) == {
        "kind",
        "places",
        "ingredients",
        "steps",
        "price",
    }

    with db.connect() as conn:
        rows = {
            str(row.id): (row.kind, row.details)
            for row in conn.execute(
                select(Item.id, Item.kind, Item.details).where(Item.user_id == user_id)
            )
        }
    assert rows == {
        old: ("place", {"places": [{"name": "Ichiran", "address": "Tokyo"}]}),
        # One Claude won't read is "other", so the next run leaves it alone.
        declined: ("other", None),
        already: ("recipe", None),
    }
