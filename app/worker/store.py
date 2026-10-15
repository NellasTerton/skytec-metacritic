"""Запись в БД: карточки игр, состояние обхода, прогоны."""

import html
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.letsplay import LetsplayResult, Video
from app.media import Media
from app.metacritic import GameDetails
from app.models import CrawlState, Game, GamePlatform, Letsplay, ReviewSummary, Run, RunItem
from app.summaries import SummaryResult
from app.worker.selection import CrawlCursor


def now() -> datetime:
    return datetime.now(UTC)


def load_cursor(session: Session, day: date) -> CrawlCursor:
    state = session.get(CrawlState, day)
    return CrawlCursor(state.phase, state.next_offset) if state else CrawlCursor()


def save_cursor(session: Session, day: date, cursor: CrawlCursor) -> None:
    state = session.get(CrawlState, day)
    if state is None:
        session.add(CrawlState(day=day, phase=cursor.phase, next_offset=cursor.next_offset))
    else:
        state.phase, state.next_offset = cursor.phase, cursor.next_offset


def processed_slugs(session: Session, day: date) -> set[str]:
    return set(session.scalars(select(RunItem.game_slug).where(RunItem.day == day)))


def summary_hashes(session: Session, slugs: list[str]) -> dict[str, dict[str, str | None]]:
    rows = session.execute(
        select(Game.slug, ReviewSummary.kind, ReviewSummary.source_hash)
        .join(ReviewSummary, ReviewSummary.game_id == Game.id)
        .where(Game.slug.in_(slugs))
    )
    out: dict[str, dict[str, str | None]] = {}
    for slug, kind, source_hash in rows:
        out.setdefault(slug, {})[kind] = source_hash
    return out


def games_without_summaries(session: Session, min_reviews: int, limit: int) -> list[str]:
    """Игры без единого резюме, у которых на основной платформе достаточно отзывов."""
    has_summary = select(ReviewSummary.id).where(ReviewSummary.game_id == Game.id).exists()
    stmt = (
        select(Game.slug)
        .where(~has_summary, (Game.critic_review_count >= min_reviews) | (Game.user_review_count >= min_reviews))
        .order_by(Game.metascore.desc().nulls_last())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def save_summaries(session: Session, slug: str, results: list[SummaryResult]) -> None:
    game = session.scalars(select(Game).where(Game.slug == slug)).one()
    existing = {s.kind: s for s in game.summaries}
    for result in results:
        row = existing.get(result.kind)
        if row is None:
            row = ReviewSummary(kind=result.kind)
            game.summaries.append(row)
        row.summary = result.summary
        row.pros = result.pros
        row.cons = result.cons
        row.review_count = result.review_count
        row.source_hash = result.source_hash
        row.model = result.model


def upsert_game(session: Session, details: GameDetails) -> Game:
    game = session.scalars(select(Game).where(Game.slug == details.slug)).one_or_none()
    if game is None:
        game = Game(slug=details.slug, title=details.title)
        session.add(game)

    lead = details.lead_platform
    game.mc_title_id = details.mc_title_id
    game.title = details.title
    game.description = details.description
    game.release_date = details.release_date
    game.rating = details.rating
    game.genres = details.genres
    game.developers = details.developers
    game.publishers = details.publishers
    game.cover_url = details.cover_url
    game.banner_url = details.banner_url
    game.video_title = details.video.title if details.video else None
    game.video_url = details.video.embed_url if details.video else None
    game.metascore = lead.metascore if lead else None
    game.critic_review_count = lead.critic_review_count if lead else None
    game.userscore = lead.userscore if lead else None
    game.user_review_count = lead.user_review_count if lead else None
    game.last_processed_at = now()

    existing = {p.slug: p for p in game.platforms}
    platforms = []
    for info in details.platforms:
        row = existing.get(info.slug) or GamePlatform(slug=info.slug)
        row.name = info.name
        row.is_lead = info.is_lead
        row.release_date = info.release_date
        row.metascore = info.metascore
        row.critic_review_count = info.critic_review_count
        row.userscore = info.userscore
        row.user_review_count = info.user_review_count
        platforms.append(row)
    game.platforms = platforms  # платформы, пропавшие из карточки, удаляются (delete-orphan)
    session.flush()
    return game


MEDIA_TTL = timedelta(days=7)


MediaTodo = tuple[str, str, dict | None]


def games_needing_media(session: Session, limit: int, slugs: list[str] | None = None) -> list[MediaTodo]:
    """(slug, title, видео Metacritic) игр без медиа или с медиа старше недели."""
    stale = Game.media_updated_at.is_(None) | (Game.media_updated_at < now() - MEDIA_TTL)
    stmt = select(Game.slug, Game.title, Game.video_title, Game.video_url).where(stale)
    if slugs is not None:
        stmt = stmt.where(Game.slug.in_(slugs))
    # Сначала игры с оценками — их чаще открывают.
    stmt = stmt.order_by(Game.metascore.desc().nulls_last(), Game.userscore.desc().nulls_last()).limit(limit)
    out: list[MediaTodo] = []
    for slug, title, video_title, video_url in session.execute(stmt):
        mc_video = {"title": html.unescape(video_title or ""), "embed_url": video_url, "url": video_url}
        out.append((slug, title, mc_video if video_url else None))
    return out


def save_media(session: Session, slug: str, media: Media) -> None:
    game = session.scalars(select(Game).where(Game.slug == slug)).one()
    game.media = media.as_json()
    game.steam_appid = media.steam_appid
    game.media_updated_at = now()


LETSPLAY_RETRY = timedelta(days=1)


def games_needing_letsplay(session: Session, limit: int) -> list[tuple[str, str, Video | None]]:
    """Игры с оценками без летсплея; упавшие — не чаще раза в сутки, с уже найденным роликом."""
    scored = Game.metascore.is_not(None) | Game.userscore.is_not(None)
    retry = (Letsplay.status == "failed") & (Letsplay.checked_at < now() - LETSPLAY_RETRY)
    rows = session.execute(
        select(Game.slug, Game.title, Letsplay)
        .outerjoin(Letsplay, Letsplay.game_id == Game.id)
        .where(scored, Letsplay.id.is_(None) | retry)
        .order_by(Game.metascore.desc().nulls_last(), Game.userscore.desc().nulls_last())
        .limit(limit)
    ).all()
    out = []
    for slug, title, row in rows:
        video = None
        if row is not None and row.video_id:
            video = Video(
                row.video_id, row.video_title or "", row.channel or "", row.view_count or 0, row.duration_s or 0
            )
        out.append((slug, title, video))
    return out


def save_letsplay(
    session: Session,
    slug: str,
    status: str,
    *,
    result: LetsplayResult | None = None,
    video: Video | None = None,
    error: str | None = None,
) -> None:
    game = session.scalars(select(Game).where(Game.slug == slug)).one()
    row = game.letsplay or Letsplay()
    game.letsplay = row
    row.status = status
    row.error = error
    row.checked_at = now()
    video = result.video if result else video
    if video:
        row.video_id, row.video_title, row.channel = video.id, video.title, video.channel
        row.view_count, row.duration_s = video.view_count, video.duration_s
    if result:
        row.method, row.transcript, row.model = result.method, result.transcript, result.model
        row.retelling, row.verdict, row.liked = result.retelling, result.verdict, result.liked
        row.pros, row.cons = result.pros, result.cons
        row.quotes = result.quotes


def games_to_recheck(session: Session, day: date, max_age_days: int, limit: int) -> list[str]:
    """Игры младше max_age_days без единой оценки, которые сегодня ещё не обрабатывали.

    Обход SEE ALL за сутки доходит лишь на ~4 дня вглубь, поэтому без перепроверки игра,
    получившая Metascore через неделю после релиза, так и осталась бы без оценок.
    """
    processed_today = select(RunItem.game_slug).where(RunItem.day == day)
    stmt = (
        select(Game.slug)
        .where(
            Game.release_date >= day - timedelta(days=max_age_days),
            Game.release_date <= day,
            Game.metascore.is_(None),
            Game.userscore.is_(None),
            Game.slug.not_in(processed_today),
        )
        .order_by(Game.release_date.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def recheck_done(session: Session, day: date) -> bool:
    stmt = select(Run.id).where(Run.phase == "recheck", Run.details["day"].astext == day.isoformat()).limit(1)
    return session.scalar(stmt) is not None
