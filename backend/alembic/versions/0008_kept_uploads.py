"""kept uploads: where an uploaded file is kept once processed

Revision ID: 0008_kept_uploads
Revises: 0007_scale
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_kept_uploads"
down_revision: str | None = "0007_scale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("file_key", sa.Text()))


def downgrade() -> None:
    op.drop_column("items", "file_key")
