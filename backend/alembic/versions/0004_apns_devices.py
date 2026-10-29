"""devices: APNs device tokens instead of Expo push tokens

Revision ID: 0004_apns_devices
Revises: 0003_multi_user
Create Date: 2026-10-29

Every existing row holds an Expo push token, which APNs cannot use, so the
rows are deleted. The native app registers its APNs token on next launch.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_apns_devices"
down_revision: str | None = "0003_multi_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM devices")
    op.alter_column("devices", "expo_push_token", new_column_name="push_token")
    op.execute(
        "ALTER TABLE devices RENAME CONSTRAINT devices_expo_push_token_key "
        "TO devices_push_token_key"
    )
    op.add_column(
        "devices",
        sa.Column("apns_env", sa.String(length=16), nullable=False, server_default="production"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM devices")
    op.drop_column("devices", "apns_env")
    op.execute(
        "ALTER TABLE devices RENAME CONSTRAINT devices_push_token_key "
        "TO devices_expo_push_token_key"
    )
    op.alter_column("devices", "push_token", new_column_name="expo_push_token")
