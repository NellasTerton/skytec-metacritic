import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

ReviewKind = Literal["critic", "user"]


class GameListItem(BaseModel):
    """Игра из выдачи finder (New Releases / SEE ALL). Платформа здесь только одна."""

    slug: str
    title: str
    release_date: dt.date | None = None
    metascore: int | None = None
    critic_review_count: int | None = None
    platform: str | None = None
    genres: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    description: str | None = None


class Page[T](BaseModel):
    total: int
    offset: int
    limit: int
    items: list[T]


class ScoreStats(BaseModel):
    """Score с нормализацией: при нуле отзывов score = None (на сайте это «tbd»)."""

    score: float | None = None
    max: int
    review_count: int = 0
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    sentiment: str | None = None


class PlatformInfo(BaseModel):
    slug: str
    name: str
    is_lead: bool = False
    release_date: dt.date | None = None
    metascore: int | None = None
    critic_review_count: int | None = None
    userscore: float | None = None
    user_review_count: int | None = None


class Video(BaseModel):
    title: str | None = None
    embed_url: str | None = None
    manifest_url: str | None = None


class Review(BaseModel):
    kind: ReviewKind
    quote: str
    score: int | None = None
    date: dt.date | None = None
    author: str | None = None
    publication: str | None = None
    url: str | None = None
    platform: str | None = None
    spoiler: bool = False


class GameDetails(BaseModel):
    slug: str
    title: str
    mc_title_id: int | None = None
    url: str
    description: str | None = None
    release_date: dt.date | None = None
    rating: str | None = None
    genres: list[str] = Field(default_factory=list)
    developers: list[str] = Field(default_factory=list)
    publishers: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    banner_url: str | None = None
    video: Video | None = None
    platforms: list[PlatformInfo] = Field(default_factory=list)
    # Оценки lead-платформы.
    critic_score: ScoreStats | None = None
    user_score: ScoreStats | None = None
    # Сбалансированная выборка из composer: до 7 отзывов в каждой корзине positive/neutral/negative.
    critic_reviews_sample: list[Review] = Field(default_factory=list)
    user_reviews_sample: list[Review] = Field(default_factory=list)
    critic_reviews_total: int = 0
    user_reviews_total: int = 0

    @property
    def lead_platform(self) -> PlatformInfo | None:
        return next((p for p in self.platforms if p.is_lead), self.platforms[0] if self.platforms else None)
