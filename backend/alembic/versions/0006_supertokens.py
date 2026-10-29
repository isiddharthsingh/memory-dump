"""supertokens: move logins to SuperTokens, keep users as the app's profile row

Revision ID: 0006_supertokens
Revises: 0005_chats
Create Date: 2026-10-29

Every existing user is imported into the SuperTokens core with its Argon2 hash
and its current id as the external user id, so people keep their password and
their library and only have to sign in once more. The import has to happen here,
before the columns that hold the hashes are dropped, so this migration needs the
core running (docker compose starts it before the api). Re-running after a
failure skips users the core already has.
"""

from collections.abc import Sequence

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config import get_settings

revision: str = "0006_supertokens"
down_revision: str | None = "0005_chats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _import_users() -> None:
    rows = (
        op.get_bind()
        .execute(sa.text("SELECT id, email, password_hash, created_at FROM users"))
        .all()
    )
    if not rows:
        return
    settings = get_settings()
    headers = {"api-key": settings.supertokens_api_key} if settings.supertokens_api_key else {}
    with httpx.Client(
        base_url=settings.supertokens_connection_uri, headers=headers, timeout=30
    ) as core:
        for user_id, email, password_hash, created_at in rows:
            mapped = core.get(
                "/recipe/userid/map", params={"userId": str(user_id), "userIdType": "EXTERNAL"}
            )
            mapped.raise_for_status()
            if mapped.json()["status"] == "OK":
                continue
            resp = core.post(
                "/bulk-import/import",
                json={
                    "externalUserId": str(user_id),
                    "loginMethods": [
                        {
                            "recipeId": "emailpassword",
                            "tenantIds": ["public"],
                            "email": email,
                            "passwordHash": password_hash,
                            "hashingAlgorithm": "argon2",
                            "isVerified": False,
                            "timeJoinedInMSSinceEpoch": int(created_at.timestamp() * 1000),
                        }
                    ],
                },
            )
            if resp.status_code != 200 or resp.json().get("status") != "OK":
                raise RuntimeError(f"SuperTokens import failed for user {user_id}: {resp.text}")


def upgrade() -> None:
    _import_users()
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_column("users", "email")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "last_login_at")
    # Ids now come from SuperTokens.
    op.alter_column("users", "id", server_default=None)


def downgrade() -> None:
    # The password hashes now live only in SuperTokens, so the old login can't
    # be rebuilt from this database.
    op.alter_column("users", "id", server_default=sa.text("gen_random_uuid()"))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=False, server_default=""))
    op.alter_column("users", "password_hash", server_default=None)
    op.add_column("users", sa.Column("email", postgresql.CITEXT()))
    op.execute("UPDATE users SET email = id::text")
    op.alter_column("users", "email", nullable=False)
    op.create_unique_constraint("users_email_key", "users", ["email"])
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
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
