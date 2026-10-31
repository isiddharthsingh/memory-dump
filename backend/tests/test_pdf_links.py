"""A shared link to a PDF is read the way an uploaded PDF is (the vision
provider, a thumbnail of its first page) instead of through trafilatura; an
HTML page goes on as before. Every chunk after the first is embedded with the
item's title and summary in front, and stored without them."""

import gzip
import io
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, insert, select

from app import thumbnails
from app.ai import embed
from app.ai.analyze import Analysis
from app.ai.providers import VisionResult
from app.config import get_settings
from app.db.models import EMBED_DIM, Item, ItemChunk
from app.extractors import webpage
from app.extractors.webpage import TooLarge, WebpageExtractor, _CappedClient, is_pdf
from app.main import app
from app.storage import get_storage
from app.workers import ingest as ingest_worker
from app.workers.ingest import ingest_file, ingest_item
from tests.conftest import signup

Handler = Callable[[httpx.Request], httpx.Response]


def _pdf() -> bytes:
    page = io.BytesIO()
    Image.new("RGB", (612, 792), "white").save(page, "PDF")
    return page.getvalue()


HTML = (
    "<html><head><title>Fallback</title>"
    '<meta property="og:title" content="A post">'
    '<meta property="og:image" content="https://example.com/cover.png">'
    "</head><body><article><p>" + "Words worth keeping. " * 20 + "</p></article></body></html>"
)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], None]:
    """Answers the extractor's fetches with `handler`, through the same
    capped client every extractor uses, at the configured cap."""

    def use(handler: Handler) -> None:
        def client(**kwargs: Any) -> httpx.AsyncClient:
            return _CappedClient(
                transport=httpx.MockTransport(handler),
                max_bytes=get_settings().extract_max_bytes,
                **kwargs,
            )

        monkeypatch.setattr(webpage, "public_client", client)

    return use


def test_a_pdf_is_known_by_its_content_type_or_its_first_bytes() -> None:
    def response(content: bytes, content_type: str) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"Content-Type": content_type})

    assert is_pdf(response(b"anything", "application/pdf"))
    assert is_pdf(response(b"anything", "Application/PDF; charset=binary"))
    assert is_pdf(response(b"\n%PDF-1.7\n...", "application/octet-stream"))
    assert is_pdf(response(_pdf(), "text/plain"))
    assert not is_pdf(response(HTML.encode(), "text/html; charset=utf-8"))
    assert not is_pdf(response(b"<p>How %PDF-1.7 files start</p>", "text/html"))


async def test_the_extractor_hands_over_a_pdf_and_keeps_html_as_it_was(
    serve: Callable[[Handler], None],
) -> None:
    pdf = _pdf()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/abs/2401.12345":
            return httpx.Response(302, headers={"Location": "/pdf/2401.12345v2"})
        if request.url.path == "/pdf/2401.12345v2":
            # Sent as a generic download: known by its first bytes.
            return httpx.Response(
                200, content=pdf, headers={"Content-Type": "application/octet-stream"}
            )
        return httpx.Response(200, text=HTML, headers={"Content-Type": "text/html"})

    serve(handler)
    content = await WebpageExtractor().extract(
        "https://arxiv.org/abs/2401.12345", share_text="For the\n\nreading group"
    )
    assert content.pdf == pdf
    # The file's name after the redirect, until the analysis names it.
    assert content.title == "2401.12345v2"
    assert content.raw_text == "User note:\nFor the\nreading group"
    assert content.thumbnail_url is None

    page = await WebpageExtractor().extract("https://example.com/post")
    assert page.pdf is None
    assert page.title == "A post"
    assert page.thumbnail_url == "https://example.com/cover.png"
    assert page.raw_text is not None
    assert page.raw_text.startswith("Title: A post\n\nBody:\nWords worth keeping.")


async def test_a_pdf_over_the_cap_is_too_large_even_compressed(
    serve: Callable[[Handler], None],
) -> None:
    big = b"%PDF-1.7\n" + b"0" * get_settings().extract_max_bytes

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(big),
            headers={"Content-Type": "application/pdf", "Content-Encoding": "gzip"},
        )

    serve(handler)
    with pytest.raises(TooLarge):
        await WebpageExtractor().extract("https://example.com/huge.pdf")


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def account(client: TestClient) -> Iterator[tuple[dict[str, str], uuid.UUID]]:
    headers = signup(client)
    yield headers, uuid.UUID(client.get("/v1/auth/me", headers=headers).json()["id"])
    client.delete("/v1/auth/me", headers=headers)


@pytest.fixture
def db() -> Iterator[Any]:
    engine = create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def embedded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What reaches Voyage, in place of Voyage."""
    texts: list[str] = []

    async def embed_texts(batch: list[str], input_type: str = "document") -> list[list[float]]:
        assert input_type == "document"
        texts.extend(batch)
        return [[0.1] * EMBED_DIM for _ in batch]

    monkeypatch.setattr(embed, "embed_texts", embed_texts)
    return texts


def _item(db: Any, user_id: uuid.UUID, url: str, **values: Any) -> uuid.UUID:
    item_id = values.pop("id", uuid.uuid4())
    with db.begin() as conn:
        conn.execute(
            insert(Item.__table__).values(
                id=item_id,
                user_id=user_id,
                url=url,
                **{"source": "web", "status": "pending", **values},
            )
        )
    return item_id


def _chunks(db: Any, item_id: uuid.UUID) -> list[str]:
    with db.connect() as conn:
        return list(
            conn.scalars(
                select(ItemChunk.text).where(ItemChunk.item_id == item_id).order_by(ItemChunk.ord)
            )
        )


def _row(db: Any, item_id: uuid.UUID) -> Any:
    with db.connect() as conn:
        return conn.execute(select(Item.__table__).where(Item.id == item_id)).one()


def test_a_pdf_link_is_read_like_an_upload_with_a_thumbnail(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
    serve: Callable[[Handler], None],
    embedded: list[str],
) -> None:
    headers, user_id = account
    pdf = _pdf()
    serve(lambda _: httpx.Response(200, content=pdf, headers={"Content-Type": "application/pdf"}))
    item_id = _item(
        db, user_id, "https://arxiv.org/pdf/2401.12345", share_text="for the reading group"
    )
    read: list[tuple[bytes, str]] = []
    # Paragraphs long enough for three chunks.
    text = "\n\n".join(
        f"Section {i}. " + " ".join(["Loss falls with compute."] * 40) for i in range(3)
    )

    class Vision:
        async def analyze_pdf(self, data: bytes, filename: str) -> VisionResult:
            read.append((data, filename))
            return VisionResult(
                Analysis("Scaling Laws", "Loss falls with compute.", ["ml"], [], [], [], []), text
            )

    async def analyze(*_: Any, **__: Any) -> Analysis:
        raise AssertionError("a PDF isn't read as a web page")

    monkeypatch.setattr(ingest_worker, "get_vision_provider", Vision)
    monkeypatch.setattr(ingest_worker, "analyze", analyze)
    run = client.portal.call  # type: ignore[union-attr]
    try:
        assert run(ingest_item, {"job_try": 1}, str(item_id), 0) == "ready"
        assert read == [(pdf, "2401.12345")]
        row = _row(db, item_id)
        assert (row.source, row.title, row.summary) == (
            "web",
            "Scaling Laws",
            "Loss falls with compute.",
        )
        # The reader's Body section (no blank lines in it), then the note.
        assert row.raw_text == (
            "Body:\n" + text.replace("\n\n", "\n") + "\n\nUser note:\nfor the reading group"
        )
        assert row.thumbnail_url == thumbnails.path(item_id)
        resp = client.get(f"/v1{row.thumbnail_url}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(resp.content)).format == "JPEG"

        # Stored plain; embedded with what the item is in front, after the first.
        chunks = _chunks(db, item_id)
        assert len(chunks) >= 2
        context = "Title: Scaling Laws\nSummary: Loss falls with compute."
        assert chunks[0].startswith("Title: Scaling Laws\n\nSummary:")
        assert embedded == [chunks[0], *(f"{context}\n\n{c}" for c in chunks[1:])]
    finally:
        run(get_storage().delete, thumbnails.key(item_id))


def test_an_html_page_is_analysed_as_before(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
    serve: Callable[[Handler], None],
    embedded: list[str],
) -> None:
    _, user_id = account
    serve(lambda _: httpx.Response(200, text=HTML, headers={"Content-Type": "text/html"}))
    item_id = _item(db, user_id, "https://example.com/post")

    class Vision:
        async def analyze_pdf(self, data: bytes, filename: str) -> VisionResult:
            raise AssertionError("not a PDF")

    async def analyze(content: Any, **_: Any) -> Analysis:
        assert content.pdf is None and "Words worth keeping." in content.raw_text
        return Analysis("A post", "Kept words.", [], [], [], [], [])

    monkeypatch.setattr(ingest_worker, "get_vision_provider", Vision)
    monkeypatch.setattr(ingest_worker, "analyze", analyze)
    run = client.portal.call  # type: ignore[union-attr]
    assert run(ingest_item, {"job_try": 1}, str(item_id), 0) == "ready"
    row = _row(db, item_id)
    assert row.thumbnail_url == "https://example.com/cover.png"
    assert row.raw_text.startswith("Title: A post\n\nBody:\nWords worth keeping.")
    # One chunk: embedded as it is stored.
    assert embedded == _chunks(db, item_id)


def test_an_uploads_later_chunks_are_embedded_with_its_title_and_summary(
    client: TestClient,
    account: tuple[dict[str, str], uuid.UUID],
    db: Any,
    monkeypatch: pytest.MonkeyPatch,
    embedded: list[str],
) -> None:
    _, user_id = account
    item_id = uuid.uuid4()
    key = f"{item_id}.png"
    _item(
        db,
        user_id,
        f"file://{item_id}/board.png",
        id=item_id,
        source="image",
        title="board.png",
        upload_key=key,
        upload_mime="image/png",
    )
    text = "\n\n".join(f"Note {i}. " + "Ship the reader first. " * 60 for i in range(2))

    class Vision:
        async def analyze_image(self, data: bytes, mime_type: str) -> VisionResult:
            return VisionResult(Analysis(None, "A whiteboard.", [], [], [], [], []), text)

    async def chunks() -> Any:
        yield b"not a PNG at all"

    monkeypatch.setattr(ingest_worker, "get_vision_provider", Vision)
    run = client.portal.call  # type: ignore[union-attr]
    run(get_storage().save, key, chunks())
    try:
        assert run(ingest_file, {"job_try": 1}, str(item_id), None, None, 0) == "ready"
        stored = _chunks(db, item_id)
        assert len(stored) >= 2
        # No title from Claude: the file's name is the one saved and embedded.
        context = "Title: board.png\nSummary: A whiteboard."
        assert embedded == [stored[0], *(f"{context}\n\n{c}" for c in stored[1:])]
    finally:
        run(get_storage().delete, key)
