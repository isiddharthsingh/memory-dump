"""scale: ingest generations, upload idempotency, chunk search columns, tombstones,
chat versions, token usage

Revision ID: 0007_scale
Revises: 0006_supertokens
Create Date: 2026-10-30

Adding the generated `fts` columns rewrites item_chunks and topic_page_chunks,
which locks them for the length of the copy: run it while nothing is ingesting.
Before the (item_id, ord) and (topic_page_id, ord) unique constraints go on,
duplicate chunks that overlapping ingest runs could leave are deleted, keeping
one per position.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_scale"
down_revision: str | None = "0006_supertokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column("ingest_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("items", sa.Column("idempotency_key", sa.Text()))
    op.add_column("items", sa.Column("upload_key", sa.Text()))
    op.add_column("items", sa.Column("upload_mime", sa.String(length=64)))
    op.add_column("items", sa.Column("wiki_status", sa.String(length=16)))
    op.create_unique_constraint(
        "uq_items_user_idempotency_key", "items", ["user_id", "idempotency_key"]
    )
    # Library pages (keyset on created_at, id) and /items/changes.
    op.create_index("ix_items_user_created", "items", ["user_id", "created_at", "id"])
    op.create_index("ix_items_user_updated", "items", ["user_id", "updated_at"])
    # What the reconciler looks for every few minutes; small, since nearly
    # every item is settled.
    op.create_index(
        "ix_items_unsettled",
        "items",
        ["updated_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_index(
        "ix_items_wiki_pending",
        "items",
        ["updated_at"],
        postgresql_where=sa.text("wiki_status = 'pending'"),
    )

    for table, parent in (("item_chunks", "item_id"), ("topic_page_chunks", "topic_page_id")):
        op.execute(
            f"""
            DELETE FROM {table} a USING {table} b
            WHERE a.{parent} = b.{parent} AND a.ord = b.ord AND a.id > b.id
            """
        )
        op.execute(
            f"""
            ALTER TABLE {table} ADD COLUMN fts tsvector
            GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED
            """
        )
        op.execute(f"CREATE INDEX ix_{table}_fts ON {table} USING gin (fts)")
    op.create_unique_constraint("uq_item_chunks_item_ord", "item_chunks", ["item_id", "ord"])
    op.create_unique_constraint(
        "uq_topic_page_chunks_page_ord", "topic_page_chunks", ["topic_page_id", "ord"]
    )

    op.create_table(
        "item_tombstones",
        sa.Column("item_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_item_tombstones_user_deleted", "item_tombstones", ["user_id", "deleted_at"])

    op.add_column("chats", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))

    op.create_table(
        "llm_usage",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("model", sa.Text(), primary_key=True),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("llm_usage")
    op.drop_column("chats", "version")
    op.drop_index("ix_item_tombstones_user_deleted", table_name="item_tombstones")
    op.drop_table("item_tombstones")
    op.drop_constraint("uq_topic_page_chunks_page_ord", "topic_page_chunks", type_="unique")
    op.drop_constraint("uq_item_chunks_item_ord", "item_chunks", type_="unique")
    for table in ("item_chunks", "topic_page_chunks"):
        op.execute(f"DROP INDEX ix_{table}_fts")
        op.drop_column(table, "fts")
    op.drop_index("ix_items_wiki_pending", table_name="items")
    op.drop_index("ix_items_unsettled", table_name="items")
    op.drop_index("ix_items_user_updated", table_name="items")
    op.drop_index("ix_items_user_created", table_name="items")
    op.drop_constraint("uq_items_user_idempotency_key", "items", type_="unique")
    for column in (
        "wiki_status",
        "upload_mime",
        "upload_key",
        "idempotency_key",
        "ingest_generation",
    ):
        op.drop_column("items", column)
