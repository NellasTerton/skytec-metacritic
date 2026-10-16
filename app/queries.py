"""Запросы сайта к БД."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Game, GamePlatform, ReviewSummary, Run, RunItem

PAGE_SIZE = 24
# Подписи — законченные фразы: в закрытом списке видно только выбранную, и по ней должно быть
# понятно, что это порядок, а не фильтр.
SORTS = {
    "release": "Сначала новые",
    "metascore": "Выше Metascore",
    "userscore": "Выше оценка игроков",
    "title": "По названию, А–Я",
}
DEFAULT_SORT = "release"
SIMILAR_LIMIT = 6
FRESH_STEP = 24  # блок «Свежие релизы, оценок пока нет»: столько игр и столько же по «Показать ещё»
FRESH_MAX = 240


@dataclass
class GameList:
    games: list[Game]
    total: int
    page: int
    pages: int


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(q: str, platform: str):
    """Поиск и фильтр по платформе; с платформой оценки берутся именно этой платформы."""
    stmt: Select = select(Game)
    metascore, userscore = Game.metascore, Game.userscore
    if platform:
        stmt = stmt.join(GamePlatform, GamePlatform.game_id == Game.id).where(GamePlatform.slug == platform)
        metascore, userscore = GamePlatform.metascore, GamePlatform.userscore
    if q.strip():
        stmt = stmt.where(Game.title.ilike(f"%{_escape_like(q.strip())}%", escape="\\"))
    return stmt, metascore, userscore


def list_games(
    session: Session, *, q: str = "", platform: str = "", sort: str = DEFAULT_SORT, page: int = 1
) -> GameList:
    """Игры с оценками (Metascore или User score) — основной список главной."""
    stmt, metascore, userscore = _filtered(q, platform)
    stmt = stmt.where(metascore.is_not(None) | userscore.is_not(None))

    order = {
        "metascore": [metascore.desc().nulls_last(), userscore.desc().nulls_last()],
        "userscore": [userscore.desc().nulls_last(), metascore.desc().nulls_last()],
        "release": [Game.release_date.desc().nulls_last(), metascore.desc().nulls_last()],
        "title": [Game.title.asc()],
    }.get(sort, [Game.release_date.desc().nulls_last()])

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


def fresh_releases(
    session: Session, *, today: date, q: str = "", platform: str = "", limit: int = FRESH_STEP
) -> tuple[list[Game], int]:
    """Вышедшие игры, у которых оценок ещё нет, — новые сверху. Возвращает (игры, сколько всего)."""
    stmt, metascore, userscore = _filtered(q, platform)
    stmt = stmt.where(metascore.is_(None), userscore.is_(None), Game.release_date <= today)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    games = session.scalars(
        stmt.options(selectinload(Game.platforms)).order_by(Game.release_date.desc(), Game.id).limit(limit)
    ).all()
    return list(games), total


def released_ago(day: date | None, today: date) -> str:
    if day is None:
        return "дата выхода неизвестна"
    days = (today - day).days
    if days <= 0:
        return "вышла сегодня"
    if days == 1:
        return "вышла вчера"
    return f"вышла {days} {plural(days, 'день', 'дня', 'дней')} назад"


def platform_options(session: Session) -> list[tuple[str, str, int]]:
    stmt = select(GamePlatform.slug, func.min(GamePlatform.name), func.count()).group_by(GamePlatform.slug)
    rows = session.execute(stmt.order_by(func.count().desc(), GamePlatform.slug))
    return [(slug, name, count) for slug, name, count in rows]


def get_game(session: Session, slug: str) -> Game | None:
    return session.scalars(
        select(Game)
        .where(Game.slug == slug)
        .options(selectinload(Game.platforms), selectinload(Game.summaries), selectinload(Game.letsplay))
    ).one_or_none()


EMBEDDING_CANDIDATES = 40
OVERLAP_CANDIDATES = 100
NEUTRAL_SIMILARITY = 0.5  # у игры ещё нет эмбеддинга — описание не помогает и не мешает
RELEVANCE_MARGIN = 0.2  # насколько хуже лучшего может быть игра, которую поднимаем за наличие оценок


def _has_score(game: Game) -> bool:
    return game.metascore is not None or game.userscore is not None


def rank_similar(
    game: Game, candidates: list[tuple[Game, float | None]], limit: int = SIMILAR_LIMIT
) -> list[tuple[Game, list[str]]]:
    """Сходство описаний (косинус эмбеддингов) + общие жанры + общий разработчик.

    Сначала отбираем самые релевантные (вдвое больше, чем покажем), и уже среди них игры с
    оценками ставим выше: иначе любая игра с оценкой обгоняла бы действительно похожую без неё.
    candidates — (игра, косинусное сходство или None, если эмбеддинга нет).
    """
    genres, devs = set(game.genres or []), set(game.developers or [])
    others = [(other, sim) for other, sim in candidates if other.id != game.id]
    # Сходство Gemini лежит в узком диапазоне (~0.75–0.85), поэтому «похожее описание» — у трёх ближайших.
    closest = {
        other.id for other, sim in sorted((c for c in others if c[1] is not None), key=lambda c: c[1], reverse=True)[:3]
    }
    ranked = []
    for other, similarity in others:
        common_genres = sorted(genres & set(other.genres or []))
        common_devs = sorted(devs & set(other.developers or []))
        union = genres | set(other.genres or [])
        jaccard = len(common_genres) / len(union) if union else 0.0
        text_similarity = similarity if similarity is not None else NEUTRAL_SIMILARITY
        score = text_similarity + 0.3 * jaccard + 0.15 * bool(common_devs)
        reasons = [*(f"разработчик {d}" for d in common_devs), *common_genres]
        if other.id in closest:
            reasons.insert(0, "похожее описание")
        ranked.append((score, other, reasons))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return []
    # Поднимаем игры с оценками только среди близких к лучшему результату.
    best = ranked[0][0]
    shortlist = [item for item in ranked[: limit * 2] if item[0] >= best - RELEVANCE_MARGIN]
    shortlist.sort(key=lambda item: _has_score(item[1]), reverse=True)  # стабильно: внутри групп — по score
    rest = [item for item in ranked if item not in shortlist]
    return [(other, reasons) for _, other, reasons in (shortlist + rest)[:limit]]


def similar_games(session: Session, game: Game, limit: int = SIMILAR_LIMIT) -> list[tuple[Game, list[str]]]:
    """Кандидаты: ближайшие по эмбеддингу описания и игры с общими жанрами или разработчиком."""
    embedding = session.scalar(select(Game.embedding).where(Game.id == game.id))
    similarity = (1 - Game.embedding.cosine_distance(embedding)) if embedding is not None else None
    columns = (Game, similarity.label("similarity")) if similarity is not None else (Game,)

    found: dict[int, tuple[Game, float | None]] = {}
    if similarity is not None:
        nearest = session.execute(
            select(*columns)
            .where(Game.id != game.id, Game.embedding.is_not(None))
            .order_by(Game.embedding.cosine_distance(embedding))
            .limit(EMBEDDING_CANDIDATES)
        )
        found.update({other.id: (other, sim) for other, sim in nearest})
    if game.genres or game.developers:
        overlap = session.execute(
            select(*columns)
            .where(Game.id != game.id, Game.genres.overlap(game.genres) | Game.developers.overlap(game.developers))
            .order_by(Game.metascore.desc().nulls_last())
            .limit(OVERLAP_CANDIDATES)
        )
        for row in overlap:
            other, sim = (row[0], row[1]) if similarity is not None else (row[0], None)
            found.setdefault(other.id, (other, sim))
    return rank_similar(game, list(found.values()), limit)


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


def _run_dict(run: Run, now: datetime, games_count: int = 0) -> dict:
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
        "games_count": games_count,
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
    # Первый прогон сбора за день (New Releases) — чтобы сверить его с metacritic.com/game/,
    # даже когда он уже ушёл из последних 15.
    first_today = session.scalars(
        select(Run).where(Run.started_at >= day_start, Run.phase.in_(COLLECT_PHASES)).order_by(Run.started_at).limit(1)
    ).first()
    counts = _games_counts(session, [*runs, *([first_today] if first_today else [])])
    return {
        "now": now.isoformat(),
        "totals": totals,
        "runs": [_run_dict(r, now, counts.get(r.id, 0)) for r in runs],
        "first_today": _run_dict(first_today, now, counts.get(first_today.id, 0)) if first_today else None,
    }


COLLECT_PHASES = ("new_releases", "browse")
METACRITIC_GAME_URL = "https://www.metacritic.com/game/{slug}/"
SOURCES = {
    "new_releases": "New Releases",
    "browse": "SEE ALL",
    "recheck": "перепроверка",
    "seed": "начальное наполнение",
}


def _games_counts(session: Session, runs: list[Run]) -> dict[int, int]:
    """Сколько игр раскрывается у прогона: строки run_items, а у прогонов без них — details.slugs."""
    ids = [r.id for r in runs]
    counts = dict(
        session.execute(select(RunItem.run_id, func.count()).where(RunItem.run_id.in_(ids)).group_by(RunItem.run_id))
        .tuples()
        .all()
    )
    for run in runs:
        counts.setdefault(run.id, len((run.details or {}).get("slugs") or []))
    return counts


def run_games(session: Session, run_id: int) -> dict | None:
    """Игры прогона в порядке выборки (для New Releases — порядок блока на metacritic.com/game/).

    status: new — игра появилась в базе в этом прогоне, updated — уже была и обновлена,
    failed — карточку забрать не удалось. У прогонов без сбора (резюме и т. п.) status = None.
    """
    run = session.get(Run, run_id)
    if run is None:
        return None
    items = session.scalars(select(RunItem).where(RunItem.run_id == run_id).order_by(RunItem.id)).all()
    order = (run.details or {}).get("slugs") or []
    slugs = list(dict.fromkeys([*order, *(i.game_slug for i in items)]))
    by_slug = {i.game_slug: i for i in items}
    games = {
        g.slug: g
        for g in session.execute(select(Game.slug, Game.title, Game.first_seen_at).where(Game.slug.in_(slugs))).all()
    }
    out = []
    for position, slug in enumerate(slugs, 1):
        item, game = by_slug.get(slug), games.get(slug)
        status = None
        if item is not None:
            if item.status != "success":
                status = "failed"
            else:
                status = "new" if game is not None and game.first_seen_at >= run.started_at else "updated"
        out.append(
            {
                "position": position,
                "slug": slug,
                "title": game.title if game else slug,
                "url": METACRITIC_GAME_URL.format(slug=slug),
                "source": SOURCES.get(item.source, item.source) if item else None,
                "status": status,
                "error": item.error if item else None,
            }
        )
    return {"id": run.id, "phase": run.phase, "started_at": run.started_at.isoformat(), "games": out}


def hero_image(session: Session) -> str | None:
    """Баннер игры с лучшей оценкой — фон шапки списка."""
    return session.scalar(
        select(Game.banner_url)
        .where(Game.banner_url.is_not(None), Game.metascore.is_not(None))
        .order_by(Game.metascore.desc(), Game.userscore.desc().nulls_last())
        .limit(1)
    )


def page_window(page: int, pages: int, radius: int = 1) -> list[int | None]:
    """Номера страниц для пагинации: первая, последняя и соседи текущей; None — многоточие."""
    keep = {1, pages, *range(page - radius, page + radius + 1)}
    out: list[int | None] = []
    for n in range(1, pages + 1):
        if n in keep:
            out.append(n)
        elif out and out[-1] is not None:
            out.append(None)
    return out


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 игра, 2 игры, 5 игр, 21 игра."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_views(n: int | None) -> str:
    """443990 → «444 тыс. просмотров», 1234567 → «1,2 млн просмотров»."""
    n = n or 0
    if n >= 1_000_000:
        value = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{value} млн просмотров"
    if n >= 1_000:
        return f"{round(n / 1000)} тыс. просмотров"
    return f"{n} {plural(n, 'просмотр', 'просмотра', 'просмотров')}"


def format_duration(seconds: int | None) -> str:
    """5460 → «1 ч 31 мин», 2280 → «38 мин»."""
    minutes = round((seconds or 0) / 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
