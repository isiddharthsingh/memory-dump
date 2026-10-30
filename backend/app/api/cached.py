"""Conditional GETs: a body with an ETag, and 304 for a client that already
has it."""

from __future__ import annotations

import hashlib

from fastapi import Request, Response
from pydantic import BaseModel


def cached_json(request: Request, body: BaseModel) -> Response:
    """`no-cache` lets the client keep its copy but ask each time; asking
    costs a 304 with no body when nothing changed."""
    content = body.model_dump_json().encode()
    etag = f'W/"{hashlib.sha256(content).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    sent = {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}
    if etag in sent or "*" in sent:
        return Response(status_code=304, headers=headers)
    return Response(content, media_type="application/json", headers=headers)
