"""game media: trailers and screenshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "games", sa.Column("media", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False)
    )
    op.add_column("games", sa.Column("steam_appid", sa.Integer()))
    op.add_column("games", sa.Column("media_updated_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("games", "media_updated_at")
    op.drop_column("games", "steam_appid")
    op.drop_column("games", "media")
