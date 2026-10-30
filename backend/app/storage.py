"""Where uploaded files wait between the API and the worker.

STORAGE_BACKEND=local keeps them under UPLOADS_DIR, a volume the api and worker
containers must share, so both run on one host. STORAGE_BACKEND=s3 keeps them
in an S3-compatible bucket (S3, R2, MinIO), so they needn't. The worker deletes
a file once it has the text out of it; a file whose item was never processed is
swept after ABANDONED_AFTER (locally by the reconciler, in a bucket by a
lifecycle rule that expires objects after a few days).
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any, Protocol

import anyio

from app.config import get_settings

ABANDONED_AFTER_S = 7 * 24 * 3600


class Storage(Protocol):
    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def sweep(self) -> int: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = anyio.Path(root)

    def _path(self, key: str) -> anyio.Path:
        # Keys are generated from item ids, never from client input; this keeps
        # a bad one from leaving the directory anyway.
        if "/" in key or key.startswith("."):
            raise ValueError(f"bad storage key: {key!r}")
        return self.root / key

    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        await self.root.mkdir(parents=True, exist_ok=True)
        async with await anyio.open_file(self._path(key), "wb") as f:
            async for chunk in chunks:
                await f.write(chunk)

    async def read(self, key: str) -> bytes:
        return await self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        await self._path(key).unlink(missing_ok=True)

    async def sweep(self) -> int:
        """Deletes files older than ABANDONED_AFTER_S. Returns how many."""
        if not await self.root.exists():
            return 0
        cutoff = time.time() - ABANDONED_AFTER_S
        swept = 0
        async for path in self.root.iterdir():
            if (await path.stat()).st_mtime < cutoff:
                await path.unlink(missing_ok=True)
                swept += 1
        return swept


class S3Storage:
    """boto3 is synchronous, so each call runs in a thread."""

    def __init__(self, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self.client = client

    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        # Spooled to disk past 8 MB, then sent as a multipart upload when big.
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as buf:
            async for chunk in chunks:
                await asyncio.to_thread(buf.write, chunk)
            buf.seek(0)
            await asyncio.to_thread(self.client.upload_fileobj, buf, self.bucket, key)

    async def read(self, key: str) -> bytes:
        def get() -> bytes:
            return bytes(self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read())

        return await asyncio.to_thread(get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def sweep(self) -> int:
        # The bucket's lifecycle rule does this.
        return 0


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        import boto3  # type: ignore[import-untyped]

        client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            region_name=s.s3_region or None,
            aws_access_key_id=s.s3_access_key_id or None,
            aws_secret_access_key=s.s3_secret_access_key or None,
        )
        return S3Storage(s.s3_bucket, client)
    if s.storage_backend == "local":
        return LocalStorage(s.uploads_dir)
    raise ValueError(f"Unknown STORAGE_BACKEND: {s.storage_backend!r}")


async def legacy_read(path: str) -> bytes:
    """A file queued by an older API build, which passed the worker a path."""
    return await anyio.Path(path).read_bytes()


async def legacy_delete(path: str) -> None:
    await anyio.Path(path).unlink(missing_ok=True)
