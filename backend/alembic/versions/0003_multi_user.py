"""multi-user auth: users, refresh_tokens, scope existing data by user_id

Revision ID: 0003_multi_user
Revises: 0002_devices
Create Date: 2026-05-24

Fresh-start migration: drops any existing items, item_chunks, topic_pages,
topic_page_chunks, and devices before adding the NOT NULL user_id column.
Bring your own backfill if you ever want to keep data through this point.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_multi_user"
down_revision: str | None = "0002_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CITEXT for case-insensitive email comparisons.
    op.execute('CREATE EXTENSION IF NOT EXISTS "citext"')

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    # Fresh-start wipe: all existing rows must go before we add NOT NULL
    # user_id (we have no way to attribute them to a user). The dependent
    # tables are listed first so CASCADE isn't needed, but TRUNCATE ...
    # CASCADE catches any FKs we might have missed.
    op.execute(
        "TRUNCATE topic_page_chunks, topic_pages, item_chunks, items, devices RESTART IDENTITY CASCADE"
    )

    op.add_column(
        "items",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_items_user_id", "items", ["user_id"])
    op.create_unique_constraint("uq_items_user_url", "items", ["user_id", "url"])

    op.add_column(
        "item_chunks",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_item_chunks_user_id", "item_chunks", ["user_id"])

    op.add_column(
        "topic_pages",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_topic_pages_user_id", "topic_pages", ["user_id"])
    # Replace the global slug-unique constraint with (user_id, slug).
    op.drop_constraint("topic_pages_slug_key", "topic_pages", type_="unique")
    op.create_unique_constraint(
        "uq_topic_pages_user_slug", "topic_pages", ["user_id", "slug"]
    )

    op.add_column(
        "topic_page_chunks",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_topic_page_chunks_user_id", "topic_page_chunks", ["user_id"])

    op.add_column(
        "devices",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_devices_user_id", table_name="devices")
    op.drop_column("devices", "user_id")

    op.drop_index("ix_topic_page_chunks_user_id", table_name="topic_page_chunks")
    op.drop_column("topic_page_chunks", "user_id")

    op.drop_constraint("uq_topic_pages_user_slug", "topic_pages", type_="unique")
    op.create_unique_constraint("topic_pages_slug_key", "topic_pages", ["slug"])
    op.drop_index("ix_topic_pages_user_id", table_name="topic_pages")
    op.drop_column("topic_pages", "user_id")

    op.drop_index("ix_item_chunks_user_id", table_name="item_chunks")
    op.drop_column("item_chunks", "user_id")

    op.drop_constraint("uq_items_user_url", "items", type_="unique")
    op.drop_index("ix_items_user_id", table_name="items")
    op.drop_column("items", "user_id")

    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
