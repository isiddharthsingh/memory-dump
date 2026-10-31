"""initial schema: items, item_chunks, topic_pages, topic_page_chunks

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBED_DIM = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("title", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column("tags", postgresql.ARRAY(sa.Text())),
        sa.Column("people", postgresql.ARRAY(sa.Text())),
        sa.Column("handles", postgresql.ARRAY(sa.Text())),
        sa.Column("brands_products", postgresql.ARRAY(sa.Text())),
        sa.Column("topics", postgresql.ARRAY(sa.Text())),
        sa.Column("share_text", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_items_created_at", "items", ["created_at"])
    op.create_index("ix_items_status", "items", ["status"])
    op.create_index("ix_items_tags", "items", ["tags"], postgresql_using="gin")
    op.create_index("ix_items_people", "items", ["people"], postgresql_using="gin")
    op.create_index("ix_items_handles", "items", ["handles"], postgresql_using="gin")
    op.create_index(
        "ix_items_brands_products", "items", ["brands_products"], postgresql_using="gin"
    )
    # Generated FTS column. Postgres marks `array_to_string(text[], text)` as
    # STABLE, not IMMUTABLE, so it can't appear directly in a generated-column
    # or index expression. We wrap the whole document build in an IMMUTABLE
    # SQL function — the function body only depends on its inputs, so the
    # IMMUTABLE assertion is correct.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mdmp_item_fts_text(
            _title text, _summary text, _raw text,
            _people text[], _handles text[], _brands text[]
        ) RETURNS text AS $$
          SELECT coalesce(_title,'') || ' ' ||
                 coalesce(_summary,'') || ' ' ||
                 coalesce(_raw,'') || ' ' ||
                 coalesce(array_to_string(_people,  ' '), '') || ' ' ||
                 coalesce(array_to_string(_handles, ' '), '') || ' ' ||
                 coalesce(array_to_string(_brands,  ' '), '')
        $$ LANGUAGE SQL IMMUTABLE
        """
    )
    op.execute(
        """
        ALTER TABLE items ADD COLUMN fts_doc tsvector
        GENERATED ALWAYS AS (
          to_tsvector('english'::regconfig,
            mdmp_item_fts_text(title, summary, raw_text, people, handles, brands_products)
          )
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_items_fts ON items USING gin (fts_doc)")

    op.create_table(
        "item_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM)),
    )
    op.execute(
        "CREATE INDEX ix_item_chunks_embedding ON item_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index("ix_item_chunks_item_id", "item_chunks", ["item_id"])

    op.create_table(
        "topic_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=255), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("backlinks", postgresql.ARRAY(sa.Text())),
        sa.Column("source_item_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute(
        """
        ALTER TABLE topic_pages ADD COLUMN fts_doc tsvector
        GENERATED ALWAYS AS (
          to_tsvector('english'::regconfig, title || ' ' || body_md)
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_topic_pages_fts ON topic_pages USING gin (fts_doc)")

    op.create_table(
        "topic_page_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "topic_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM)),
    )
    op.execute(
        "CREATE INDEX ix_topic_page_chunks_embedding ON topic_page_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_index(
        "ix_topic_page_chunks_topic_page_id", "topic_page_chunks", ["topic_page_id"]
    )


def downgrade() -> None:
    op.drop_table("topic_page_chunks")
    op.drop_table("topic_pages")
    op.drop_table("item_chunks")
    op.drop_table("items")
    op.execute("DROP FUNCTION IF EXISTS mdmp_item_fts_text(text, text, text, text[], text[], text[])")
    op.execute("DROP EXTENSION IF EXISTS vector")
