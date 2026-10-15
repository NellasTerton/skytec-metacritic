"""Тесты против настоящего Postgres. Без DATABASE_URL в окружении пропускаются.

Пишут только синтетические slug (test-...) и выдуманную дату, чтобы не задеть рабочие данные.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import session_scope
from app.models import CrawlState, Game, GamePlatform, ReviewSummary, Run, RunItem
from app.worker import runner
from tests.conftest import requires_db
from tests.fakes import FakeGameClient

pytestmark = requires_db

TEST_DAY = dt.date(2000, 1, 1)


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
        s.delete(game)


@pytest.fixture
def isolated_day(monkeypatch):
    monkeypatch.setattr(runner, "service_today", lambda: TEST_DAY)
    tag = uuid.uuid4().hex[:6]
    created: list[int] = []
    yield tag, created
    with session_scope(serverless=False) as s:
        s.execute(delete(Game).where(Game.slug.like(f"test-{tag}-%")))
        s.execute(delete(CrawlState).where(CrawlState.day == TEST_DAY))
        s.execute(delete(Run).where(Run.id.in_(created)))


async def test_two_runs_new_releases_then_browse(isolated_day):
    tag, created = isolated_day
    new = [f"test-{tag}-new-{i}" for i in range(5)]
    browse = [f"test-{tag}-b-{i}" for i in range(30)]
    client = FakeGameClient(new, browse, broken={new[1]})

    first = await runner.run_once("test", client=client)
    second = await runner.run_once("test", client=client)
    created += [first, second]

    with session_scope(serverless=False) as s:
        run1, run2 = s.get_one(Run, first), s.get_one(Run, second)
        assert (run1.status, run1.phase, run1.games_found) == ("success", "new_releases", 5)
        assert (run1.games_processed, run1.games_failed) == (4, 1)
        assert (run2.phase, run2.games_found, run2.games_processed) == ("browse", 20, 20)

        items = s.scalars(select(RunItem).where(RunItem.run_id.in_([first, second]))).all()
        assert len({i.game_slug for i in items}) == 25
        assert next(i for i in items if i.game_slug == new[1]).status == "failed"

        game = s.scalars(select(Game).where(Game.slug == new[0])).one()
        assert game.developers == ["Dev Studio"] and game.metascore == 80
        assert [p.slug for p in game.platforms] == ["pc", "playstation-5"]
        assert s.get_one(CrawlState, TEST_DAY).phase == "browse"


async def test_upsert_updates_existing_game(isolated_day):
    tag, created = isolated_day
    slug = f"test-{tag}-upd"
    client = FakeGameClient([slug], [])
    created.append(await runner.run_once("test", client=client))
    with session_scope(serverless=False) as s:
        s.execute(delete(RunItem).where(RunItem.day == TEST_DAY))
        s.execute(delete(CrawlState).where(CrawlState.day == TEST_DAY))
        first_seen = s.scalars(select(Game.first_seen_at).where(Game.slug == slug)).one()
    created.append(await runner.run_once("test", client=client))
    with session_scope(serverless=False) as s:
        games = s.scalars(select(Game).where(Game.slug == slug)).all()
        assert len(games) == 1 and games[0].first_seen_at == first_seen
        assert games[0].last_processed_at > first_seen
