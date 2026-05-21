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


class ItemPatch(BaseModel):
    share_text: str | None = None


class IngestResponse(BaseModel):
    item: ItemSummary


class SearchRequest(BaseModel):
    q: str
    limit: int = 10


class SearchHit(BaseModel):
    kind: str  # "item" | "topic_page"
    id: uuid.UUID
    title: str | None
    url: str | None = None
    slug: str | None = None
    snippet: str
    score: float


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class AskRequest(BaseModel):
    question: str


class CitationOut(BaseModel):
    kind: str
    id: uuid.UUID
    title: str | None
    url: str | None = None
    slug: str | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]


class TopicPageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    backlinks: list[str] | None
    source_item_ids: list[uuid.UUID] | None
    version: int
    updated_at: datetime


class TopicPageDetail(TopicPageSummary):
    body_md: str
