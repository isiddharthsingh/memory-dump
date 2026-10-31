"""held topics: slugs an item waits on until a second item supports them

Revision ID: 0009_held_topics
Revises: 0008_kept_uploads
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_held_topics"
down_revision: str | None = "0008_kept_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("held_topics", sa.ARRAY(sa.Text())))


def downgrade() -> None:
    op.drop_column("items", "held_topics")
