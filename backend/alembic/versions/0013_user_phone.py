"""user phone: the phone number asked for at sign-up

Revision ID: 0013_user_phone
Revises: 0012_own_keys_and_spend
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_user_phone"
down_revision: str | None = "0012_own_keys_and_spend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone", sa.String(16)))


def downgrade() -> None:
    op.drop_column("users", "phone")
