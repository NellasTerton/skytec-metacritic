"""letsplays: blogger video, transcript and verdict

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "letsplays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("video_id", sa.String(32)),
        sa.Column("video_title", sa.Text()),
        sa.Column("channel", sa.Text()),
        sa.Column("view_count", sa.BigInteger()),
        sa.Column("duration_s", sa.Integer()),
        sa.Column("method", sa.String(16)),
        sa.Column("transcript", sa.Text()),
        sa.Column("retelling", sa.Text()),
        sa.Column("verdict", sa.Text()),
        sa.Column("pros", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("cons", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("liked", sa.Boolean()),
        sa.Column("model", sa.String(128)),
        sa.Column("error", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_letsplays"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], name="fk_letsplays_game_id_games", ondelete="CASCADE"),
        sa.UniqueConstraint("game_id", name="uq_letsplays_game_id"),
    )


def downgrade() -> None:
    op.drop_table("letsplays")
