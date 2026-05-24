from fastapi.testclient import TestClient

from app.main import app


def test_list_tags_requires_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/items/tags").status_code == 401


def test_retry_requires_auth() -> None:
    with TestClient(app) as client:
        assert (
            client.post("/items/00000000-0000-0000-0000-000000000000/retry").status_code == 401
        )
