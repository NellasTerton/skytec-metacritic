"""Запросы сайта к БД."""

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Game, GamePlatform

PAGE_SIZE = 24
SORTS = {
    "metascore": "Metascore",
    "userscore": "User score",
    "release": "Дата выхода",
    "title": "Название",
}
SIMILAR_LIMIT = 6


@dataclass
class GameList:
    games: list[Game]
    total: int
    page: int
    pages: int


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_games(
    session: Session, *, q: str = "", platform: str = "", sort: str = "metascore", page: int = 1
) -> GameList:
    stmt: Select = select(Game)
    # С фильтром по платформе сортируем по оценкам именно этой платформы.
    metascore, userscore = Game.metascore, Game.userscore
    if platform:
        stmt = stmt.join(GamePlatform, GamePlatform.game_id == Game.id).where(GamePlatform.slug == platform)
        metascore, userscore = GamePlatform.metascore, GamePlatform.userscore
    if q.strip():
        stmt = stmt.where(Game.title.ilike(f"%{_escape_like(q.strip())}%", escape="\\"))

    order = {
        "metascore": [metascore.desc().nulls_last(), userscore.desc().nulls_last()],
        "userscore": [userscore.desc().nulls_last(), metascore.desc().nulls_last()],
        "release": [Game.release_date.desc().nulls_last()],
        "title": [Game.title.asc()],
    }.get(sort, [metascore.desc().nulls_last()])

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 1), pages)
    games = session.scalars(
        stmt.options(selectinload(Game.platforms))
        .order_by(*order, Game.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return GameList(games=list(games), total=total, page=page, pages=pages)


def platform_options(session: Session) -> list[tuple[str, str, int]]:
    rows = session.execute(
        select(GamePlatform.slug, func.min(GamePlatform.name), func.count())
        .group_by(GamePlatform.slug)
        .order_by(func.count().desc())
    )
    return [(slug, name, count) for slug, name, count in rows]


def get_game(session: Session, slug: str) -> Game | None:
    return session.scalars(
        select(Game).where(Game.slug == slug).options(selectinload(Game.platforms), selectinload(Game.summaries))
    ).one_or_none()


def similar_games(session: Session, game: Game, limit: int = SIMILAR_LIMIT) -> list[tuple[Game, list[str]]]:
    """Похожие по общим жанрам и разработчику. Общий разработчик весит вдвое больше жанра."""
    if not game.genres and not game.developers:
        return []
    candidates = session.scalars(
        select(Game)
        .where(Game.id != game.id, Game.genres.overlap(game.genres) | Game.developers.overlap(game.developers))
        .order_by(Game.metascore.desc().nulls_last())
        .limit(300)
    ).all()

    scored = []
    for other in candidates:
        genres = sorted(set(other.genres) & set(game.genres))
        devs = sorted(set(other.developers) & set(game.developers))
        score = len(genres) + 2 * len(devs)
        reasons = [*(f"разработчик {d}" for d in devs), *genres]
        scored.append((score, other.metascore or 0, other, reasons))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [(other, reasons) for _, _, other, reasons in scored[:limit]]
