"""Тесты против настоящего Postgres (CI-сервис или Neon). Без DATABASE_URL пропускаются."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Game, GamePlatform, ReviewSummary, Run
from tests.conftest import fixture_transport, requires_db

pytestmark = requires_db


def test_game_roundtrip():
    slug = f"test-{uuid.uuid4().hex[:8]}"
    with session_scope(serverless=False) as s:
        s.add(
            Game(
                slug=slug,
                title="Test Game",
                genres=["RPG"],
                developers=["Dev"],
                release_date=dt.date(2026, 10, 15),
                metascore=90,
                userscore=8.4,
                platforms=[GamePlatform(slug="pc", name="PC", is_lead=True, metascore=90, userscore=8.4)],
                summaries=[ReviewSummary(kind="critic", summary="ok", pros=["a"], cons=["b"])],
            )
        )
    with session_scope(serverless=False) as s:
        game = s.scalars(select(Game).where(Game.slug == slug)).one()
        assert game.genres == ["RPG"] and game.userscore == pytest.approx(8.4)
        assert game.platforms[0].slug == "pc" and game.summaries[0].pros == ["a"]
        assert game.first_seen_at is not None
        s.delete(game)


async def test_worker_run_records_run(monkeypatch):
    from app.worker import runner

    original = runner.MetacriticClient

    def client_factory(**kwargs):
        return original(transport=fixture_transport(), backoff=0, **kwargs)

    monkeypatch.setattr(runner, "MetacriticClient", client_factory)
    run_id = await runner.run_once("test")
    with session_scope(serverless=False) as s:
        run = s.get_one(Run, run_id)
        assert run.status == "success" and run.games_found == 20 and run.finished_at is not None
        assert len(run.details["new_releases"]) == 20
        s.delete(run)
