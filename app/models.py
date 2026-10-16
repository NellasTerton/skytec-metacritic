from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.embeddings import DIMENSIONS

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _text_array() -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text), server_default=text("'{}'"))


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    mc_title_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    release_date: Mapped[date | None] = mapped_column(Date)
    rating: Mapped[str | None] = mapped_column(String(16))
    genres: Mapped[list[str]] = _text_array()
    developers: Mapped[list[str]] = _text_array()
    publishers: Mapped[list[str]] = _text_array()
    cover_url: Mapped[str | None] = mapped_column(Text)
    banner_url: Mapped[str | None] = mapped_column(Text)
    video_title: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(Text)
    # Трейлеры и скриншоты из внешних источников (YouTube, Steam): {"videos": [...], "screenshots": [...]}.
    media: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    steam_appid: Mapped[int | None] = mapped_column(Integer)
    media_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Эмбеддинг названия, жанров, разработчика и описания — для «похожих игр».
    embedding: Mapped[list[float] | None] = mapped_column(Vector(DIMENSIONS), deferred=True)

    # Оценки lead-платформы: по ним сортируем список. По платформам — в game_platforms.
    metascore: Mapped[int | None] = mapped_column(Integer)
    critic_review_count: Mapped[int | None] = mapped_column(Integer)
    userscore: Mapped[float | None] = mapped_column(Numeric(3, 1, asdecimal=False))
    user_review_count: Mapped[int | None] = mapped_column(Integer)

    first_seen_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    platforms: Mapped[list[GamePlatform]] = relationship(
        back_populates="game", cascade="all, delete-orphan", order_by="GamePlatform.is_lead.desc()"
    )
    summaries: Mapped[list[ReviewSummary]] = relationship(back_populates="game", cascade="all, delete-orphan")
    letsplay: Mapped[Letsplay | None] = relationship(back_populates="game", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_games_metascore", "metascore"),
        Index("ix_games_release_date", "release_date"),
    )


class GamePlatform(Base):
    __tablename__ = "game_platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    is_lead: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    release_date: Mapped[date | None] = mapped_column(Date)
    metascore: Mapped[int | None] = mapped_column(Integer)
    critic_review_count: Mapped[int | None] = mapped_column(Integer)
    userscore: Mapped[float | None] = mapped_column(Numeric(3, 1, asdecimal=False))
    user_review_count: Mapped[int | None] = mapped_column(Integer)

    game: Mapped[Game] = relationship(back_populates="platforms")

    __table_args__ = (
        UniqueConstraint("game_id", "slug"),
        Index("ix_game_platforms_slug", "slug"),
    )


class ReviewSummary(Base):
    """LLM-резюме: kind = critic | user | letsplay."""

    __tablename__ = "review_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    pros: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    cons: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    review_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Хэш входных отзывов: если не изменился, LLM повторно не вызываем.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = _updated()

    game: Mapped[Game] = relationship(back_populates="summaries")

    __table_args__ = (UniqueConstraint("game_id", "kind"),)


class Letsplay(Base):
    """Летсплей игры: ролик, расшифровка речи блогера и заключение по ней.

    status: done | not_found (ролика нет — больше не ищем) | failed (повторим позже).
    """

    __tablename__ = "letsplays"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(16))
    video_id: Mapped[str | None] = mapped_column(String(32))
    video_title: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(String(16))  # captions | video
    transcript: Mapped[str | None] = mapped_column(Text)
    retelling: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(Text)
    quotes: Mapped[list[dict]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))  # [{text, ru}]
    pros: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    cons: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    liked: Mapped[bool | None] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = _updated()

    game: Mapped[Game] = relationship(back_populates="letsplay")


class Run(Base):
    """Один прогон воркера; по этой таблице работает мониторинг."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"))
    phase: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    games_found: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    games_processed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    games_failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))

    items: Mapped[list[RunItem]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_runs_started_at", "started_at"),)


class RunItem(Base):
    """Игра, обработанная в прогоне; (day, game_slug) отвечает на «обрабатывали ли сегодня»."""

    __tablename__ = "run_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    game_slug: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()

    run: Mapped[Run] = relationship(back_populates="items")

    __table_args__ = (Index("ix_run_items_day_game_slug", "day", "game_slug"),)


class CrawlState(Base):
    """Курсор обхода на день: сначала New Releases, затем страницы SEE ALL."""

    __tablename__ = "crawl_state"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    phase: Mapped[str] = mapped_column(String(32))
    next_offset: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated_at: Mapped[datetime] = _updated()
