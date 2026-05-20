import os

os.environ.setdefault("MEMORY_DUMP_API_TOKEN", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_list_tags_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/items/tags").status_code == 401


def test_retry_requires_auth() -> None:
    with TestClient(app) as client:
        assert (
            client.post("/items/00000000-0000-0000-0000-000000000000/retry").status_code == 401
        )
