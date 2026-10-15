"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, *, default: bool = True) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.func.now() if default else None, nullable=not default
    )


def _text_array(name: str) -> sa.Column:
    return sa.Column(name, postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("mc_title_id", sa.BigInteger()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("release_date", sa.Date()),
        sa.Column("rating", sa.String(16)),
        _text_array("genres"),
        _text_array("developers"),
        _text_array("publishers"),
        sa.Column("cover_url", sa.Text()),
        sa.Column("banner_url", sa.Text()),
        sa.Column("video_title", sa.Text()),
        sa.Column("video_url", sa.Text()),
        sa.Column("metascore", sa.Integer()),
        sa.Column("critic_review_count", sa.Integer()),
        sa.Column("userscore", sa.Numeric(3, 1)),
        sa.Column("user_review_count", sa.Integer()),
        _ts("first_seen_at"),
        _ts("updated_at"),
        _ts("last_processed_at", default=False),
        sa.PrimaryKeyConstraint("id", name="pk_games"),
        sa.UniqueConstraint("slug", name="uq_games_slug"),
    )
    op.create_index("ix_games_metascore", "games", ["metascore"])
    op.create_index("ix_games_release_date", "games", ["release_date"])

    op.create_table(
        "game_platforms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_lead", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("release_date", sa.Date()),
        sa.Column("metascore", sa.Integer()),
        sa.Column("critic_review_count", sa.Integer()),
        sa.Column("userscore", sa.Numeric(3, 1)),
        sa.Column("user_review_count", sa.Integer()),
        sa.PrimaryKeyConstraint("id", name="pk_game_platforms"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], name="fk_game_platforms_game_id_games", ondelete="CASCADE"),
        sa.UniqueConstraint("game_id", "slug", name="uq_game_platforms_game_id_slug"),
    )
    op.create_index("ix_game_platforms_slug", "game_platforms", ["slug"])

    op.create_table(
        "review_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("pros", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("cons", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("review_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("source_url", sa.Text()),
        sa.Column("model", sa.String(128)),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_review_summaries"),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name="fk_review_summaries_game_id_games", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("game_id", "kind", name="uq_review_summaries_game_id_kind"),
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("phase", sa.String(32)),
        _ts("started_at"),
        _ts("finished_at", default=False),
        _ts("heartbeat_at", default=False),
        sa.Column("games_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("games_processed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("games_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_runs"),
    )
    op.create_index("ix_runs_started_at", "runs", ["started_at"])

    op.create_table(
        "run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("game_slug", sa.String(255), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_run_items"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name="fk_run_items_run_id_runs", ondelete="CASCADE"),
    )
    op.create_index("ix_run_items_day_game_slug", "run_items", ["day", "game_slug"])

    op.create_table(
        "crawl_state",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("next_offset", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("day", name="pk_crawl_state"),
    )


def downgrade() -> None:
    op.drop_table("crawl_state")
    op.drop_index("ix_run_items_day_game_slug", table_name="run_items")
    op.drop_table("run_items")
    op.drop_index("ix_runs_started_at", table_name="runs")
    op.drop_table("runs")
    op.drop_table("review_summaries")
    op.drop_index("ix_game_platforms_slug", table_name="game_platforms")
    op.drop_table("game_platforms")
    op.drop_index("ix_games_release_date", table_name="games")
    op.drop_index("ix_games_metascore", table_name="games")
    op.drop_table("games")
