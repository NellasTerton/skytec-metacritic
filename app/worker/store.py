"""Запись в БД: карточки игр, состояние обхода, прогоны."""

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.metacritic import GameDetails
from app.models import CrawlState, Game, GamePlatform, RunItem
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
