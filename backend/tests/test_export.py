"""The Obsidian export: the notes, and the zip the route sends."""

import io
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert

from app.config import get_settings
from app.db.models import Item, TopicPage
from app.export import item_name, item_note, page_note
from app.main import app
from tests.conftest import signup

X = uuid.UUID("11111111-1111-1111-1111-111111111111")
Y = uuid.UUID("22222222-2222-2222-2222-222222222222")
GONE = uuid.UUID("33333333-3333-3333-3333-333333333333")


def test_an_item_note_carries_what_is_saved_of_it() -> None:
    item = Item(
        id=X,
        url="https://example.com/ramen",
        source="instagram",
        title='Ramen: the "best" bowls',
        summary="Three shops.",
        share_text="Go hungry\nand early",
        raw_text="Caption:\nSo good.",
        tags=["ramen", "tokyo"],
        kind="place",
        details={
            "places": [{"name": "Ichiran", "address": "Shibuya"}, {"name": "Afuri", "address": ""}]
        },
        # 23:30 UTC is already the next day in Tokyo.
        created_at=datetime(2026, 10, 29, 23, 30, tzinfo=UTC),
        done_at=datetime(2026, 11, 1, 12, tzinfo=UTC),
    )
    path, note = item_note(item, ZoneInfo("Asia/Tokyo"))
    assert path == "Memory Dump/Items/ramen-the-best-bowls-11111111.md"
    assert note == "\n".join(
        [
            "---",
            'title: "Ramen: the \\"best\\" bowls"',
            'url: "https://example.com/ramen"',
            'source: "instagram"',
            'kind: "place"',
            'saved: "2026-10-30"',
            'done: "2026-11-01"',
            'tags: ["ramen", "tokyo"]',
            "---",
            "",
            '# Ramen: the "best" bowls',
            "",
            "[Open the original](https://example.com/ramen)",
            "",
            "> [!note] My note",
            "> Go hungry",
            "> and early",
            "",
            "## Summary",
            "",
            "Three shops.",
            "",
            "## Places",
            "",
            "- Ichiran, Shibuya",
            "- Afuri",
            "",
            "## Text",
            "",
            "Caption:",
            "So good.",
            "",
        ]
    )


def test_an_upload_has_no_link_and_a_bare_item_still_has_a_name() -> None:
    upload = Item(id=X, url=f"file://{X}/scan.pdf", source="pdf", title="Scan")
    _, note = item_note(upload, UTC)
    assert "url:" not in note and "Open the original" not in note
    assert note.endswith("# Scan\n")
    assert item_name(Y, None) == "item-22222222"
    assert item_name(Y, "日本語 / ラーメン?").endswith("-22222222")


def test_a_page_note_links_its_citations_to_the_items_notes() -> None:
    page = TopicPage(
        slug="ramen",
        title="Ramen",
        body_md=(
            f"Tonkotsu is rich [item:{Y}]. Shoyu is light [item:{X}] [item:{Y}].\n\n"
            f"A claim from an item deleted since [item:{GONE}]. See [[tokyo]]."
        ),
        corrections=[{"id": "c", "text": "Ichiran\nclosed.", "at": "2026-11-01T00:00:00+00:00"}],
        updated_at=datetime(2026, 11, 2, tzinfo=UTC),
    )
    path, note = page_note(page, {X: "Shoyu guide", Y: None}, UTC)
    assert path == "Memory Dump/Wiki/ramen.md"
    assert note == "\n".join(
        [
            "---",
            'title: "Ramen"',
            'aliases: ["Ramen"]',
            'updated: "2026-11-02"',
            "---",
            "",
            "# Ramen",
            "",
            "Tonkotsu is rich [[item-22222222|1]]. Shoyu is light"
            " [[shoyu-guide-11111111|2]] [[item-22222222|1]].",
            "",
            "A claim from an item deleted since. See [[tokyo]].",
            "",
            "## Sources",
            "",
            "1. [[item-22222222|Untitled]]",
            "2. [[shoyu-guide-11111111|Shoyu guide]]",
            "",
            "## My corrections",
            "",
            "- Ichiran closed.",
            "",
        ]
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


def test_the_export_is_a_zip_of_the_users_ready_items_and_written_pages(
    client: TestClient, db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import export as export_api

    # Two items a query, so the export pages through them.
    monkeypatch.setattr(export_api, "BATCH", 2)
    mine, stranger = signup(client), signup(client)
    try:
        user_id, stranger_id = (
            uuid.UUID(client.get("/auth/me", headers=h).json()["id"]) for h in (mine, stranger)
        )
        ids = [uuid.uuid4() for _ in range(5)]
        rows = [
            {"id": i, "user_id": user_id, "title": f"Mine {n}", "status": "ready"}
            for n, i in enumerate(ids)
        ]
        rows.append(
            {"id": uuid.uuid4(), "user_id": user_id, "title": "Pending", "status": "pending"}
        )
        rows.append(
            {"id": uuid.uuid4(), "user_id": stranger_id, "title": "Theirs", "status": "ready"}
        )
        with db.begin() as conn:
            conn.execute(
                insert(Item.__table__),
                [{**r, "url": f"https://example.com/{r['id']}", "source": "web"} for r in rows],
            )
            conn.execute(
                insert(TopicPage.__table__),
                [
                    {
                        "user_id": user_id,
                        "slug": "topic",
                        "title": "Topic",
                        "body_md": f"A claim [item:{ids[0]}].",
                        "source_item_ids": [ids[0]],
                    },
                    {
                        "user_id": user_id,
                        "slug": "stub",
                        "title": "Stub",
                        "body_md": "_pending_",
                        "source_item_ids": None,
                    },
                    {
                        "user_id": stranger_id,
                        "slug": "theirs",
                        "title": "Theirs",
                        "body_md": "Theirs.",
                        "source_item_ids": [rows[-1]["id"]],
                    },
                ],
            )

        resp = client.get("/v1/export/obsidian", headers=mine)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert resp.headers["content-disposition"].startswith('attachment; filename="memory-dump-')
        with zipfile.ZipFile(io.BytesIO(resp.content)) as vault:
            names = set(vault.namelist())
            page = vault.read("Memory Dump/Wiki/topic.md").decode()
        assert names == {"Memory Dump/Wiki/topic.md"} | {
            f"Memory Dump/Items/mine-{n}-{str(i)[:8]}.md" for n, i in enumerate(ids)
        }
        assert f"A claim [[mine-0-{str(ids[0])[:8]}|1]]." in page
        assert client.get("/v1/export/obsidian").status_code == 401
    finally:
        for headers in (mine, stranger):
            client.delete("/auth/me", headers=headers)
