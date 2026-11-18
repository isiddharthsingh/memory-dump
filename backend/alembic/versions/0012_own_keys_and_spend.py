"""own keys and spend: a user's own provider keys, what each call cost and who paid, and the admin flag

Revision ID: 0012_own_keys_and_spend
Revises: 0011_kinds_and_corrections
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_own_keys_and_spend"
down_revision: str | None = "0011_kinds_and_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("users", sa.Column("ai_provider", sa.String(16)))
    op.add_column("users", sa.Column("ai_models", postgresql.JSONB()))
    op.add_column("users", sa.Column("own_spend_limit_usd", sa.Numeric(10, 2)))

    op.create_table(
        "provider_keys",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(8), nullable=False),
        sa.Column("region", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Usage from before this counts as the server's and costs nothing: the
    # month's free allowance starts from the calls made after the upgrade.
    op.add_column(
        "llm_usage",
        sa.Column("billed_to", sa.String(8), nullable=False, server_default="platform"),
    )
    op.add_column(
        "llm_usage", sa.Column("cost_usd", sa.Numeric(16, 8), nullable=False, server_default="0")
    )
    op.drop_constraint("llm_usage_pkey", "llm_usage", type_="primary")
    op.create_primary_key("llm_usage_pkey", "llm_usage", ["user_id", "day", "model", "billed_to"])


def downgrade() -> None:
    # A day's rows for one model, paid by the server and by the user's key,
    # become one again.
    op.execute(
        """
        WITH merged AS (
            SELECT user_id, day, model, sum(calls) AS calls,
                   sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                   sum(cache_read_tokens) AS cache_read_tokens,
                   sum(cache_write_tokens) AS cache_write_tokens
            FROM llm_usage GROUP BY user_id, day, model HAVING count(*) > 1
        ), gone AS (
            DELETE FROM llm_usage u USING merged m
            WHERE u.user_id = m.user_id AND u.day = m.day AND u.model = m.model
        )
        INSERT INTO llm_usage (user_id, day, model, calls, input_tokens, output_tokens,
                               cache_read_tokens, cache_write_tokens)
        SELECT user_id, day, model, calls, input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens
        FROM merged
        """
    )
    op.drop_constraint("llm_usage_pkey", "llm_usage", type_="primary")
    op.create_primary_key("llm_usage_pkey", "llm_usage", ["user_id", "day", "model"])
    op.drop_column("llm_usage", "cost_usd")
    op.drop_column("llm_usage", "billed_to")
    op.drop_table("provider_keys")
    op.drop_column("users", "own_spend_limit_usd")
    op.drop_column("users", "ai_models")
    op.drop_column("users", "ai_provider")
    op.drop_column("users", "is_admin")
