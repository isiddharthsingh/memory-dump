"""canonical urls: saved links lose their share parameters, as new shares do

Revision ID: 0010_canonical_urls
Revises: 0009_held_topics
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.extractors.base import canonical_url

revision: str = "0010_canonical_urls"
down_revision: str | None = "0009_held_topics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, user_id, url FROM items WHERE url NOT LIKE 'file://%' "
            "ORDER BY created_at, id"
        )
    ).all()
    taken = {(row.user_id, row.url) for row in rows}
    for row in rows:
        url = canonical_url(row.url)
        # Of a post saved twice before this, the copy that already has the
        # link (else the first saved) keeps it, and the other keeps its link
        # as it was, so (user_id, url) stays unique; deleting that copy in the
        # app takes it out of the wiki too.
        if url == row.url or (row.user_id, url) in taken:
            continue
        conn.execute(
            sa.text("UPDATE items SET url = :url WHERE id = :id"), {"url": url, "id": row.id}
        )
        taken.discard((row.user_id, row.url))
        taken.add((row.user_id, url))


def downgrade() -> None:
    # The share parameters carried nothing the items need.
    pass
