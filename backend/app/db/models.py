from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBED_DIM = 1024  # voyage-3-large


class Base(DeclarativeBase):
    pass


class User(Base):
    """The app's side of an account. SuperTokens owns the email, password and
    sessions; `id` is the SuperTokens user ID, and every owned row hangs off it."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_items_user_url"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_items_user_idempotency_key"),
        Index("ix_items_user_created", "user_id", "created_at", "id"),
        Index("ix_items_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)

    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    people: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    handles: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    brands_products: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    topics: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    share_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    # Bumped each time the item is queued for ingest. A job carries the
    # generation it was queued for and writes nothing once a newer request
    # (an edited note, a retry) has moved it on.
    ingest_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The client's Idempotency-Key for an upload, so a resend returns this item.
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    # Where an uploaded file waits for the worker (app/storage.py), and its type.
    upload_key: Mapped[str | None] = mapped_column(Text)
    upload_mime: Mapped[str | None] = mapped_column(String(64))
    # Where the file is kept once processed. Null for links and for uploads
    # processed before files were kept.
    file_key: Mapped[str | None] = mapped_column(Text)
    # "pending" once ingest is done until synthesis folds the item into the
    # wiki, then "done", or "failed" when synthesis gave up. Null for items
    # from before it was tracked.
    wiki_status: Mapped[str | None] = mapped_column(String(16))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chunks: Mapped[list[ItemChunk]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ItemChunk(Base):
    __tablename__ = "item_chunks"
    __table_args__ = (UniqueConstraint("item_id", "ord", name="uq_item_chunks_item_ord"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalized from items.user_id so retrieval SQL can scope chunks
    # without joining items. Cascade deletes keep it in sync.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    item: Mapped[Item] = relationship(back_populates="chunks")


class TopicPage(Base):
    __tablename__ = "topic_pages"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_topic_pages_user_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    backlinks: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    source_item_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chunks: Mapped[list[TopicPageChunk]] = relationship(
        back_populates="topic_page", cascade="all, delete-orphan"
    )


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Hex APNs device token, and which APNs host it belongs to: Xcode builds
    # get sandbox tokens, TestFlight/App Store builds get production ones.
    push_token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    apns_env: Mapped[str] = mapped_column(String(16), nullable=False, server_default="production")
    platform: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Chat(Base):
    """One Ask conversation, kept per user so it follows the account to any device."""

    __tablename__ = "chats"
    __table_args__ = (Index("ix_chats_user_id_updated_at", "user_id", "updated_at"),)

    # The app picks the id, so it can save a chat before the first answer arrives.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The questions and answers in order; each one is a ChatTurn (app/api/schemas.py).
    turns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # Bumped by every save; a save that names an older version is refused.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TopicPageChunk(Base):
    __tablename__ = "topic_page_chunks"
    __table_args__ = (
        UniqueConstraint("topic_page_id", "ord", name="uq_topic_page_chunks_page_ord"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("topic_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized from topic_pages.user_id for the same retrieval-scoping
    # reason as item_chunks.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))

    topic_page: Mapped[TopicPage] = relationship(back_populates="chunks")


# How long a deleted item's tombstone is kept.
TOMBSTONE_DAYS = 30


class ItemTombstone(Base):
    """A deleted item, kept for TOMBSTONE_DAYS so /items/changes can tell
    clients to drop their copy, and so a list cursor naming it still works."""

    __tablename__ = "item_tombstones"
    __table_args__ = (Index("ix_item_tombstones_user_deleted", "user_id", "deleted_at"),)

    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LlmUsage(Base):
    """Tokens a user spent per day and model (app/usage.py)."""

    __tablename__ = "llm_usage"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
