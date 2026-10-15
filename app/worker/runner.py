"""Один прогон воркера: выбрать до 20 игр, забрать карточки, сохранить, отчитаться в runs."""

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import session_scope
from app.metacritic import GameDetails, MetacriticClient
from app.models import Run, RunItem
from app.worker import store
from app.worker.selection import select_games

log = logging.getLogger(__name__)

BATCH_SIZE = 20


def service_today() -> date:
    return datetime.now(ZoneInfo(get_settings().service_tz)).date()


async def _fetch(client: MetacriticClient, slug: str) -> tuple[str, GameDetails | Exception]:
    try:
        return slug, await client.game(slug)
    except Exception as e:  # noqa: BLE001 - ошибка одной игры не должна ронять прогон
        return slug, e


async def run_once(trigger: str, *, client: MetacriticClient | None = None) -> int:
    settings = get_settings()
    day = service_today()
    with session_scope(serverless=False) as session:
        run = Run(trigger=trigger, status="running", heartbeat_at=store.now(), details={"day": day.isoformat()})
        session.add(run)
        session.flush()
        run_id = run.id
        cursor = store.load_cursor(session, day)
        done = store.processed_slugs(session, day)
    log.info(
        "run #%d started: trigger=%s day=%s phase=%s processed_today=%d",
        run_id,
        trigger,
        day,
        cursor.phase,
        len(done),
    )

    own_client = client is None
    client = client or MetacriticClient(
        concurrency=settings.metacritic_concurrency,
        timeout=settings.metacritic_timeout,
        retries=settings.metacritic_retries,
    )
    try:
        selection = await select_games(client, cursor, done, batch_size=BATCH_SIZE)
        sources = {slug: source for slug, source in selection.picked}
        with session_scope(serverless=False) as session:
            store.save_cursor(session, day, selection.cursor)
            run = session.get_one(Run, run_id)
            run.phase = selection.picked[0][1] if selection.picked else selection.cursor.phase
            run.games_found = len(selection.picked)
            run.heartbeat_at = store.now()
            run.details = {
                **run.details,
                "slugs": list(sources),
                "next_offset": selection.cursor.next_offset,
                "pages_fetched": selection.pages_fetched,
                "exhausted": selection.exhausted,
            }
        log.info("run #%d picked %d games (%s)", run_id, len(sources), run.phase)

        processed = failed = 0
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
                    processed += 1
                session.add(item)
                run = session.get_one(Run, run_id)
                run.games_processed, run.games_failed = processed, failed
                run.heartbeat_at = store.now()
                run.message = f"last: {slug}"
    except Exception as e:
        with session_scope(serverless=False) as session:
            run = session.get_one(Run, run_id)
            run.status, run.finished_at, run.message = "failed", store.now(), f"{type(e).__name__}: {e}"[:2000]
        log.exception("run #%d failed", run_id)
        raise
    finally:
        if own_client:
            await client.aclose()

    with session_scope(serverless=False) as session:
        run = session.get_one(Run, run_id)
        run.status = "success"
        run.finished_at = run.heartbeat_at = store.now()
        run.message = f"processed {processed}, failed {failed}"
    log.info("run #%d finished: processed=%d failed=%d", run_id, processed, failed)
    return run_id
