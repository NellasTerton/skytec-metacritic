"""Запросы сайта к БД."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Game, GamePlatform, ReviewSummary, Run, RunItem

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
    session: Session,
    *,
    q: str = "",
    platform: str = "",
    sort: str = "metascore",
    scored_only: bool = True,
    page: int = 1,
) -> GameList:
    stmt: Select = select(Game)
    # С фильтром по платформе фильтруем и сортируем по оценкам именно этой платформы.
    metascore, userscore = Game.metascore, Game.userscore
    if platform:
        stmt = stmt.join(GamePlatform, GamePlatform.game_id == Game.id).where(GamePlatform.slug == platform)
        metascore, userscore = GamePlatform.metascore, GamePlatform.userscore
    if q.strip():
        stmt = stmt.where(Game.title.ilike(f"%{_escape_like(q.strip())}%", escape="\\"))
    unscored = metascore.is_(None) & userscore.is_(None)
    if scored_only:
        stmt = stmt.where(~unscored)

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
        # Игры без оценок всегда в конце, при любой сортировке (false < true).
        .order_by(unscored.asc(), *order, Game.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return GameList(games=list(games), total=total, page=page, pages=pages)


def platform_options(session: Session, *, scored_only: bool = True) -> list[tuple[str, str, int]]:
    stmt = select(GamePlatform.slug, func.min(GamePlatform.name), func.count()).group_by(GamePlatform.slug)
    if scored_only:
        stmt = stmt.where(GamePlatform.metascore.is_not(None) | GamePlatform.userscore.is_not(None))
    rows = session.execute(stmt.order_by(func.count().desc(), GamePlatform.slug))
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


AUTO_TRIGGERS = ("cron", "schedule")
STALE_RUN_MINUTES = 15


def active_run(session: Session) -> Run | None:
    """Прогон, который идёт прямо сейчас (живой heartbeat); зависшие не считаются."""
    fresh = datetime.now(UTC) - timedelta(minutes=STALE_RUN_MINUTES)
    return session.scalars(
        select(Run).where(Run.status == "running", Run.heartbeat_at > fresh).order_by(Run.started_at.desc()).limit(1)
    ).first()


def auto_run_blocker(session: Session, min_interval_minutes: int) -> str | None:
    """Причина не запускать автоматический прогон сейчас, или None.

    Блокирует, если прогон ещё идёт (живой heartbeat) или автоматический прогон уже был
    за последние `min_interval_minutes` минут.
    """
    now = datetime.now(UTC)
    if running := active_run(session):
        return f"run #{running.id} is still running"
    recent = session.scalars(
        select(Run)
        .where(Run.trigger.in_(AUTO_TRIGGERS), Run.started_at > now - timedelta(minutes=min_interval_minutes))
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    if recent:
        return f"automatic run #{recent.id} already started at {recent.started_at:%H:%M} UTC"
    return None


def _run_dict(run: Run, now: datetime) -> dict:
    stale = run.status == "running" and (run.heartbeat_at or run.started_at) < now - timedelta(
        minutes=STALE_RUN_MINUTES
    )
    end = run.finished_at or now
    summaries = (run.details or {}).get("summaries") or {}
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": "stale" if stale else run.status,
        "phase": run.phase,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_s": int((end - run.started_at).total_seconds()),
        "heartbeat_age_s": int((now - run.heartbeat_at).total_seconds()) if run.heartbeat_at else None,
        "found": run.games_found,
        "processed": run.games_processed,
        "failed": run.games_failed,
        "summaries_done": summaries.get("done", 0),
        "summaries_needed": summaries.get("needed", 0),
        "summaries_skipped": summaries.get("skipped"),
        "message": run.message,
    }


def monitor_status(session: Session, *, day_start: datetime, recent: int = 15) -> dict:
    """Данные для страницы мониторинга. day_start — начало «сегодня» в часовом поясе сервиса."""
    now = datetime.now(UTC)
    runs = session.scalars(select(Run).order_by(Run.started_at.desc()).limit(recent)).all()
    scored = Game.metascore.is_not(None) | Game.userscore.is_not(None)
    totals = {
        "games": session.scalar(select(func.count()).select_from(Game)) or 0,
        "games_scored": session.scalar(select(func.count()).select_from(Game).where(scored)) or 0,
        "summaries": session.scalar(select(func.count()).select_from(ReviewSummary)) or 0,
        "runs_today": session.scalar(select(func.count()).select_from(Run).where(Run.started_at >= day_start)) or 0,
        "processed_today": session.scalar(
            select(func.count(func.distinct(RunItem.game_slug))).where(RunItem.created_at >= day_start)
        )
        or 0,
    }
    return {"now": now.isoformat(), "totals": totals, "runs": [_run_dict(r, now) for r in runs]}
