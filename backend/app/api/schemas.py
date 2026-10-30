from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class IngestRequest(BaseModel):
    url: HttpUrl
    share_text: str | None = Field(default=None, max_length=20_000)


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
    error: str | None = None
    created_at: datetime


class ItemDetail(ItemSummary):
    raw_text: str | None
    people: list[str] | None
    handles: list[str] | None
    brands_products: list[str] | None
    topics: list[str] | None
    share_text: str | None


class ItemPatch(BaseModel):
    share_text: str | None = Field(default=None, max_length=20_000)


class IngestResponse(BaseModel):
    item: ItemSummary


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(default=10, ge=1, le=50)


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
    question: str = Field(min_length=1, max_length=10_000)


class CitationOut(BaseModel):
    kind: str
    id: uuid.UUID
    title: str | None = None
    url: str | None = None
    slug: str | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]


class ChatError(BaseModel):
    title: str = Field(max_length=500)
    detail: str = Field(default="", max_length=4000)


class ChatTurn(BaseModel):
    """One question and its answer. Keys are single words because the app
    sends request bodies without snake_case conversion."""

    id: uuid.UUID
    question: str = Field(min_length=1, max_length=20_000)
    answer: str = Field(default="", max_length=200_000)
    citations: list[CitationOut] = Field(default_factory=list, max_length=200)
    # "streaming" is saved when the question is asked, so it isn't lost if
    # the app quits before the answer finishes.
    status: Literal["streaming", "done", "failed"]
    error: ChatError | None = None


class ChatIn(BaseModel):
    turns: list[ChatTurn] = Field(min_length=1, max_length=500)
    # The version the app last saw (0 for a chat it is creating): a save over a
    # newer one is refused with 409. Without it the save always wins.
    version: int | None = Field(default=None, ge=0)


class ChatSummary(BaseModel):
    id: uuid.UUID
    questions: list[str]
    created_at: datetime
    updated_at: datetime
    version: int


class ChatOut(BaseModel):
    id: uuid.UUID
    turns: list[ChatTurn]
    created_at: datetime
    updated_at: datetime
    version: int


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


class ItemChanges(BaseModel):
    """What changed in the library since a `since`. `full` means the
    deletions from then aren't all kept any more: walk the list instead."""

    full: bool
    changed: list[uuid.UUID]
    deleted: list[uuid.UUID]
    # Pass back as `since` next time. UTC, ending in Z (no "+" to escape).
    until: str


class RelatedResponse(BaseModel):
    in_wiki: list[TopicPageSummary]
    related: list[ItemSummary]
