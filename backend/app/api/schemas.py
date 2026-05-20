from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class IngestRequest(BaseModel):
    url: HttpUrl
    share_text: str | None = None


class ItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    source: str
    status: str
    title: str | None
    summary: str | None
    thumbnail_url: str | None
    tags: list[str] | None
    created_at: datetime


class ItemDetail(ItemSummary):
    raw_text: str | None
    people: list[str] | None
    handles: list[str] | None
    brands_products: list[str] | None
    topics: list[str] | None
    share_text: str | None
    error: str | None


class IngestResponse(BaseModel):
    item: ItemSummary
