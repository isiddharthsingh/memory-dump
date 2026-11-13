"""kinds and corrections: what a save is for, whether it's done, and the user's corrections to a wiki page

Revision ID: 0011_kinds_and_corrections
Revises: 0010_canonical_urls
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_kinds_and_corrections"
down_revision: str | None = "0010_canonical_urls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("kind", sa.String(16)))
    op.add_column("items", sa.Column("details", postgresql.JSONB()))
    op.add_column("items", sa.Column("done_at", sa.DateTime(timezone=True)))
    op.add_column("topic_pages", sa.Column("corrections", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("topic_pages", "corrections")
    op.drop_column("items", "done_at")
    op.drop_column("items", "details")
    op.drop_column("items", "kind")
