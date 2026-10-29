"""Ask history saved per user.

Run via `docker compose exec api pytest tests/test_chats.py`.
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import signup, unique_email


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def two_users(client: TestClient) -> Iterator[tuple[dict[str, str], dict[str, str]]]:
    a = signup(client, unique_email("chat"))
    b = signup(client, unique_email("chat"))
    yield a, b
    client.delete("/auth/me", headers=a)
    client.delete("/auth/me", headers=b)


def _turn(question: str, **extra: object) -> dict:
    return {"id": str(uuid.uuid4()), "question": question, "status": "done", **extra}


def test_save_list_open_and_delete(client: TestClient, two_users: tuple) -> None:
    a, _ = two_users
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    citation = {"kind": "item", "id": str(uuid.uuid4()), "title": "How to Do Great Work"}
    # The app leaves nil fields out entirely.
    untitled = {"kind": "topic_page", "id": str(uuid.uuid4()), "slug": "writing"}
    turn = _turn("What did I save?", answer="Two essays [item:x]", citations=[citation, untitled])

    resp = client.put(f"/chats/{first}", json={"turns": [turn]}, headers=a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["questions"] == ["What did I save?"]
    client.put(f"/chats/{second}", json={"turns": [_turn("Newer chat")]}, headers=a)

    listed = client.get("/chats", headers=a).json()
    assert [c["id"] for c in listed] == [second, first]
    assert listed[1]["questions"] == ["What did I save?"]
    assert "turns" not in listed[1]

    opened = client.get(f"/chats/{first}", headers=a).json()
    assert opened["turns"][0]["answer"] == "Two essays [item:x]"
    assert opened["turns"][0]["citations"][0]["title"] == "How to Do Great Work"

    assert client.delete(f"/chats/{first}", headers=a).status_code == 204
    assert client.get(f"/chats/{first}", headers=a).status_code == 404
    assert [c["id"] for c in client.get("/chats", headers=a).json()] == [second]


def test_saving_again_replaces_the_turns(client: TestClient, two_users: tuple) -> None:
    a, _ = two_users
    chat = str(uuid.uuid4())
    asked = _turn("Why?", status="streaming")
    client.put(f"/chats/{chat}", json={"turns": [asked]}, headers=a)
    failed = {**asked, "status": "failed", "error": {"title": "Offline", "detail": "No network"}}
    resp = client.put(f"/chats/{chat}", json={"turns": [failed, _turn("And now?")]}, headers=a)
    assert resp.status_code == 200, resp.text

    turns = client.get(f"/chats/{chat}", headers=a).json()["turns"]
    assert [t["question"] for t in turns] == ["Why?", "And now?"]
    assert turns[0]["status"] == "failed"
    assert turns[0]["error"]["title"] == "Offline"


def test_chats_are_private(client: TestClient, two_users: tuple) -> None:
    a, b = two_users
    chat = str(uuid.uuid4())
    client.put(f"/chats/{chat}", json={"turns": [_turn("Mine")]}, headers=a)

    assert client.get("/chats", headers=b).json() == []
    assert client.get(f"/chats/{chat}", headers=b).status_code == 404
    assert client.delete(f"/chats/{chat}", headers=b).status_code == 404
    # Reusing someone else's id must not overwrite their chat.
    resp = client.put(f"/chats/{chat}", json={"turns": [_turn("Theirs")]}, headers=b)
    assert resp.status_code == 404, resp.text
    assert client.get(f"/chats/{chat}", headers=a).json()["turns"][0]["question"] == "Mine"


@pytest.mark.parametrize(
    "body",
    [
        {"turns": []},
        {"turns": [_turn("")]},
        {"turns": [_turn("Hi", status="thinking")]},
        {"turns": [{"question": "No id", "status": "done"}]},
    ],
)
def test_rejects_bad_chats(client: TestClient, two_users: tuple, body: dict) -> None:
    a, _ = two_users
    resp = client.put(f"/chats/{uuid.uuid4()}", json=body, headers=a)
    assert resp.status_code == 422, resp.text
    assert client.get("/chats", headers=a).json() == []


def test_needs_sign_in(client: TestClient) -> None:
    assert client.get("/chats").status_code == 401
