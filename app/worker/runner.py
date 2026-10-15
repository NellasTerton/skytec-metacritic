"""Прогон воркера. Этап 0: фиксирует прогон в БД и забирает список New Releases.

Выборка «20 необработанных за сегодня», карточки игр и резюме — следующие этапы.
"""

import logging
from datetime import UTC, datetime

from app.config import get_settings
from app.db import session_scope
from app.metacritic import MetacriticClient
from app.models import Run

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def run_once(trigger: str) -> int:
    settings = get_settings()
    with session_scope(serverless=False) as session:
        run = Run(trigger=trigger, status="running", phase="new_releases", heartbeat_at=_now())
        session.add(run)
        session.flush()
        run_id = run.id
    log.info("run #%d started (trigger=%s)", run_id, trigger)

    try:
        async with MetacriticClient(
            concurrency=settings.metacritic_concurrency,
            timeout=settings.metacritic_timeout,
            retries=settings.metacritic_retries,
        ) as client:
            items = await client.new_releases()
    except Exception as e:
        with session_scope(serverless=False) as session:
            run = session.get_one(Run, run_id)
            run.status, run.finished_at, run.message = "failed", _now(), f"{type(e).__name__}: {e}"
        log.exception("run #%d failed", run_id)
        raise

    with session_scope(serverless=False) as session:
        run = session.get_one(Run, run_id)
        run.status = "success"
        run.finished_at = run.heartbeat_at = _now()
        run.games_found = len(items)
        run.details = {"new_releases": [i.slug for i in items]}
    log.info("run #%d finished: %d new releases", run_id, len(items))
    return run_id
