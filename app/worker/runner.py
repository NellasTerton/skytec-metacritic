"""Один прогон воркера: выбрать до 20 игр, забрать карточки, сохранить, написать резюме отзывов."""

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.db import session_scope
from app.llm import LLMClient, LLMError, providers_from_settings
from app.media import collect_media
from app.metacritic import GameDetails, MetacriticClient
from app.metacritic.client import BROWSER_UA
from app.models import Run, RunItem
from app.summaries import MIN_REVIEWS, kinds_to_update, summarize
from app.worker import store
from app.worker.selection import select_games

log = logging.getLogger(__name__)

BATCH_SIZE = 20


def service_today() -> date:
    return datetime.now(ZoneInfo(get_settings().service_tz)).date()


def _update_run(run_id: int, **fields) -> None:
    with session_scope(serverless=False) as session:
        run = session.get_one(Run, run_id)
        details = fields.pop("details", None)
        if details:
            run.details = {**run.details, **details}
        for key, value in fields.items():
            setattr(run, key, value)
        run.heartbeat_at = store.now()


async def _fetch(client: MetacriticClient, slug: str) -> tuple[str, GameDetails | Exception]:
    try:
        return slug, await client.game(slug)
    except Exception as e:  # noqa: BLE001 - ошибка одной игры не должна ронять прогон
        return slug, e


async def _collect(run_id: int, day: date, client: MetacriticClient, sources: dict[str, str]) -> list[GameDetails]:
    fetched: list[GameDetails] = []
    failed = 0
    for next_done in asyncio.as_completed([_fetch(client, slug) for slug in sources]):
        slug, result = await next_done
        with session_scope(serverless=False) as session:
            item = RunItem(run_id=run_id, day=day, game_slug=slug, source=sources[slug], status="success")
            if isinstance(result, Exception):
                item.status, item.error = "failed", f"{type(result).__name__}: {result}"[:2000]
                failed += 1
                log.warning("run #%d: %s failed: %s", run_id, slug, item.error)
            else:
                store.upsert_game(session, result)
                fetched.append(result)
            session.add(item)
            run = session.get_one(Run, run_id)
            run.games_processed, run.games_failed = len(fetched), failed
            run.heartbeat_at = store.now()
            run.message = f"собрано: {slug}"
    return fetched


MEDIA_CONCURRENCY = 4


async def _media(run_id: int, todo: list[tuple[str, str, dict | None]]) -> dict:
    """Трейлеры (YouTube) и скриншоты (Steam) для игр из todo."""
    stats = {"needed": len(todo), "done": 0, "videos": 0, "screenshots": 0}
    if not todo:
        return stats
    semaphore = asyncio.Semaphore(MEDIA_CONCURRENCY)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": BROWSER_UA}) as http:

        async def one(slug: str, title: str, mc_video: dict | None):
            async with semaphore:
                return slug, await collect_media(http, title, mc_video)

        for next_done in asyncio.as_completed([one(*item) for item in todo]):
            slug, media = await next_done
            with session_scope(serverless=False) as session:
                store.save_media(session, slug, media)
            stats["done"] += 1
            stats["videos"] += bool(media.videos)
            stats["screenshots"] += bool(media.screenshots)
            _update_run(run_id, message=f"медиа {stats['done']}/{len(todo)}: {slug}", details={"media": stats})
    log.info("run #%d media: %s", run_id, stats)
    return stats


async def _summarize(run_id: int, llm: LLMClient | None, games: list[GameDetails], limit: int) -> dict:
    if llm is None:
        log.info("run #%d: no LLM provider configured, summaries skipped", run_id)
        return {"skipped": "no LLM provider configured"}

    with session_scope(serverless=False) as session:
        known = store.summary_hashes(session, [g.slug for g in games])
    todo = [(g, kinds) for g in games if (kinds := kinds_to_update(g, known.get(g.slug, {})))][:limit]
    stats = {"needed": len(todo), "done": 0, "failed": 0}

    for i, (game, kinds) in enumerate(todo, 1):
        if not llm.available:
            stats["stopped"] = "all LLM providers exhausted"
            break
        _update_run(run_id, message=f"резюме {i}/{len(todo)}: {game.slug}")
        try:
            results = await summarize(llm, game, kinds)
        except LLMError as e:
            stats["failed"] += 1
            log.warning("run #%d: summary for %s failed: %s", run_id, game.slug, e)
            continue
        with session_scope(serverless=False) as session:
            store.save_summaries(session, game.slug, results)
        stats["done"] += 1
        _update_run(run_id, details={"summaries": stats})
    log.info("run #%d summaries: %s", run_id, stats)
    return stats


async def run_once(
    trigger: str, *, client: MetacriticClient | None = None, llm: LLMClient | None = None, use_llm: bool = True
) -> int:
    settings = get_settings()
    day = service_today()
    with session_scope(serverless=False) as session:
        run = Run(trigger=trigger, status="running", heartbeat_at=store.now(), details={"day": day.isoformat()})
        session.add(run)
        session.flush()
        run_id = run.id
        cursor = store.load_cursor(session, day)
        done = store.processed_slugs(session, day)
    log.info("run #%d started: trigger=%s day=%s phase=%s today=%d", run_id, trigger, day, cursor.phase, len(done))

    own_client, own_llm = client is None, llm is None
    client = client or MetacriticClient(
        concurrency=settings.metacritic_concurrency,
        timeout=settings.metacritic_timeout,
        retries=settings.metacritic_retries,
    )
    if llm is None and use_llm and (providers := providers_from_settings(settings)):
        llm = LLMClient(providers)
    try:
        selection = await select_games(client, cursor, done, batch_size=BATCH_SIZE)
        sources = dict(selection.picked)
        with session_scope(serverless=False) as session:
            store.save_cursor(session, day, selection.cursor)
        phase = selection.picked[0][1] if selection.picked else selection.cursor.phase
        _update_run(
            run_id,
            phase=phase,
            games_found=len(sources),
            details={
                "slugs": list(sources),
                "next_offset": selection.cursor.next_offset,
                "pages_fetched": selection.pages_fetched,
                "exhausted": selection.exhausted,
            },
        )
        log.info("run #%d picked %d games (%s)", run_id, len(sources), phase)

        games = await _collect(run_id, day, client, sources)
        with session_scope(serverless=False) as session:
            media_todo = store.games_needing_media(session, BATCH_SIZE, [g.slug for g in games])
        await _media(run_id, media_todo)
        summaries = await _summarize(run_id, llm, games, settings.summaries_per_run)
    except Exception as e:
        _update_run(run_id, status="failed", finished_at=store.now(), message=f"{type(e).__name__}: {e}"[:2000])
        log.exception("run #%d failed", run_id)
        raise
    finally:
        if own_client:
            await client.aclose()
        if own_llm and llm is not None:
            await llm.aclose()

    message = f"игр {len(games)}/{len(sources)}, резюме {summaries.get('done', 0)}"
    _update_run(run_id, status="success", finished_at=store.now(), message=message, details={"summaries": summaries})
    log.info("run #%d finished: %s", run_id, message)
    return run_id


async def summarize_backlog(
    limit: int, *, client: MetacriticClient | None = None, llm: LLMClient | None = None, trigger: str = "backfill"
) -> int | None:
    """Резюме для игр, которые уже лежат в БД без резюме (например, собраны до появления ключа LLM).

    Если резюмировать нечего или нет LLM, прогон не создаётся — чтобы не засорять мониторинг.
    """
    settings = get_settings()
    own_client, own_llm = client is None, llm is None
    providers = providers_from_settings(settings) if llm is None else []
    if llm is None and not providers:
        log.info("summary backlog skipped: no LLM provider configured")
        return None
    with session_scope(serverless=False) as session:
        slugs = store.games_without_summaries(session, MIN_REVIEWS, limit)
        if not slugs:
            log.info("summary backlog is empty")
            return None
        run = Run(trigger=trigger, status="running", phase="summaries", games_found=len(slugs))
        run.heartbeat_at = store.now()
        session.add(run)
        session.flush()
        run_id = run.id
    log.info("run #%d: summary backlog of %d games", run_id, len(slugs))

    llm = llm or LLMClient(providers)
    client = client or MetacriticClient(concurrency=settings.metacritic_concurrency)
    try:
        results = await asyncio.gather(*(_fetch(client, slug) for slug in slugs))
        games = [r for _, r in results if isinstance(r, GameDetails)]
        _update_run(run_id, games_processed=len(games), games_failed=len(slugs) - len(games))
        summaries = await _summarize(run_id, llm, games, limit)
    except Exception as e:
        _update_run(run_id, status="failed", finished_at=store.now(), message=f"{type(e).__name__}: {e}"[:2000])
        raise
    finally:
        if own_client:
            await client.aclose()
        if own_llm and llm is not None:
            await llm.aclose()
    _update_run(
        run_id,
        status="success",
        finished_at=store.now(),
        message=f"резюме {summaries.get('done', 0)}/{len(slugs)}",
        details={"summaries": summaries, "slugs": slugs},
    )
    return run_id


async def media_backlog(limit: int, *, trigger: str = "backfill") -> int | None:
    """Трейлеры и скриншоты для игр в БД, у которых их нет или они устарели."""
    with session_scope(serverless=False) as session:
        todo = store.games_needing_media(session, limit)
        if not todo:
            log.info("media backlog is empty")
            return None
        run = Run(trigger=trigger, status="running", phase="media", games_found=len(todo))
        run.heartbeat_at = store.now()
        session.add(run)
        session.flush()
        run_id = run.id
    try:
        stats = await _media(run_id, todo)
    except Exception as e:
        _update_run(run_id, status="failed", finished_at=store.now(), message=f"{type(e).__name__}: {e}"[:2000])
        raise
    _update_run(
        run_id,
        status="success",
        finished_at=store.now(),
        games_processed=stats["done"],
        message=f"медиа {stats['done']}/{len(todo)}: видео {stats['videos']}, скриншоты {stats['screenshots']}",
    )
    return run_id
