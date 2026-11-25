"""ai consent: when the user agreed to their content going to AI services

Revision ID: 0014_ai_consent
Revises: 0013_user_phone
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_ai_consent"
down_revision: str | None = "0013_user_phone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Null for every existing account: consent is asked for, never assumed.
    op.add_column("users", sa.Column("ai_consent_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("users", "ai_consent_at")
