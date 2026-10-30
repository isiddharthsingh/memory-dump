"""Where uploaded files are kept, with their thumbnails (app/thumbnails.py).

STORAGE_BACKEND=local keeps them under UPLOADS_DIR, a volume the api and worker
containers must share, so both run on one host. STORAGE_BACKEND=s3 keeps them
in an S3-compatible bucket (the compose stack's SeaweedFS, S3, R2, MinIO), so
they needn't. Every key starts with its item's id. The worker keeps an image or
PDF once it has the text out of it, and deletes it when processing fails; the
API deletes an item's files with the item. Whatever is left behind (an upload
whose row never landed, the files of a deleted account, a delete that couldn't
reach storage) the reconciler sweeps once it is ABANDONED_AFTER_S old and its
item is gone, so the bucket must hold nothing else.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

import anyio

from app.config import get_settings

# A file is saved before its item's row is committed: give it this long.
ABANDONED_AFTER_S = 3600


class Storage(Protocol):
    async def save(self, key: str, chunks: AsyncIterator[bytes]) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def keys_older_than(self, seconds: float) -> list[str]: ...


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
        """Raises FileNotFoundError when there's no such file."""
        return await self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        await self._path(key).unlink(missing_ok=True)

    async def keys_older_than(self, seconds: float) -> list[str]:
        if not await self.root.exists():
            return []
        cutoff = time.time() - seconds
        return [
            path.name async for path in self.root.iterdir() if (await path.stat()).st_mtime < cutoff
        ]


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
        """Raises FileNotFoundError when there's no such object."""

        def get() -> bytes:
            try:
                obj = self.client.get_object(Bucket=self.bucket, Key=key)
            except self.client.exceptions.NoSuchKey:
                raise FileNotFoundError(key) from None
            return bytes(obj["Body"].read())

        return await asyncio.to_thread(get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def keys_older_than(self, seconds: float) -> list[str]:
        def listed() -> list[str]:
            cutoff = datetime.now(UTC) - timedelta(seconds=seconds)
            pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
            try:
                return [
                    obj["Key"]
                    for page in pages
                    for obj in page.get("Contents", [])
                    if obj["LastModified"] < cutoff
                ]
            except self.client.exceptions.NoSuchBucket:
                # SeaweedFS creates it with the first upload.
                return []

        return await asyncio.to_thread(listed)


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
