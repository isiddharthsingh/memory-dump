"""Shared pytest setup.

Tests run inside the compose stack (`docker compose exec api pytest`), against
the real Postgres, Redis and SuperTokens core. The helpers sign up throwaway
accounts the way the app does: SuperTokens' /auth/signup, header-based tokens.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Every test signs up from the same address; the sign-in limit has its own test.
os.environ.setdefault("RATE_LIMIT_AUTH_PER_HOUR", "0")

PASSWORD = "super-secret-pw-123"


class Jobs(list[tuple[Any, ...]]):
    """Jobs queued during a test, as (function, *args, kwargs), instead of
    going to Redis, so a running worker can't change an item mid-test."""


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Iterator[Jobs]:
    from app import queue

    queued = Jobs()

    class Pool:
        async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> object:
            kwargs.pop("request_id", None)
            queued.append((function, *args, kwargs))
            return object()

    async def pool() -> Pool:
        return Pool()

    monkeypatch.setattr(queue, "get_arq_pool", pool)
    yield queued
    # The uploads no worker got to.
    from app.api.ingest import _MIME_TO_EXT
    from app.storage import get_storage

    for function, item_id, *_ in queued:
        if function == "ingest_file":
            for ext in set(_MIME_TO_EXT.values()):
                asyncio.run(get_storage().delete(f"{item_id}.{ext}"))


def alive(pid: int) -> bool:
    """Whether `pid` runs, and isn't a zombie (Linux: /proc)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


@pytest.fixture
def child_script(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], Path]]:
    """Points `sys.executable` at a shell script with the given body, standing
    in for the child a module starts (`python -m …`); returns the script's
    folder. Children run as the sandbox user, so the folder is one it can run
    from and write in: pytest's own are root's alone."""
    folder = Path(tempfile.mkdtemp(prefix="child-"))
    folder.chmod(0o777)
    script = folder / "child.sh"

    def write(body: str) -> Path:
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)
        monkeypatch.setattr(sys, "executable", str(script))
        return folder

    yield write
    shutil.rmtree(folder, ignore_errors=True)


def unique_email(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def form(email: str, password: str = PASSWORD, **extra: str) -> dict:
    fields = {"email": email, "password": password, **extra}
    return {"formFields": [{"id": k, "value": v} for k, v in fields.items()]}


def signup(client: TestClient, email: str | None = None, **extra: str) -> dict[str, str]:
    """Signs up a fresh user; returns the Authorization header for its session."""
    resp = client.post(
        "/auth/signup",
        json=form(email or unique_email(), **extra),
        headers={"st-auth-mode": "header"},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "OK", resp.text
    return {"Authorization": f"Bearer {resp.headers['st-access-token']}"}
