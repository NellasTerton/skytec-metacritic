"""Тесты против настоящего Postgres. Без DATABASE_URL в окружении пропускаются.

Пишут только синтетические slug (test-...) и выдуманную дату, чтобы не задеть рабочие данные.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import session_scope
from app.metacritic import parse
from app.models import CrawlState, Game, GamePlatform, ReviewSummary, Run, RunItem
from app.worker import runner
from tests.conftest import load, requires_db
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


class FakeLLM:
    available = True

    def __init__(self):
        self.calls = 0

    async def complete_json(self, system, user, **kw):
        self.calls += 1
        return {
            "critic": {"summary": "Критикам нравится", "pros": ["мир"], "cons": ["камера"]},
            "user": {"summary": "Игрокам нравится", "pros": ["боссы"], "cons": []},
        }, "fake/model"


async def test_summaries_written_once_per_review_set(isolated_day):
    tag, created = isolated_day
    slug = f"test-{tag}-sum"
    client = FakeGameClient([slug], [])
    real = client.game

    async def game_with_reviews(s):
        details = await real(s)
        sample = parse.parse_composer(load("composer_elden-ring"))
        details.critic_reviews_sample, details.user_reviews_sample = (
            sample.critic_reviews_sample,
            sample.user_reviews_sample,
        )
        return details

    client.game = game_with_reviews
    llm = FakeLLM()
    created.append(await runner.run_once("test", client=client, llm=llm))
    with session_scope(serverless=False) as s:
        game = s.scalars(select(Game).where(Game.slug == slug)).one()
        summaries = {x.kind: x for x in game.summaries}
        assert summaries["critic"].pros == ["мир"] and summaries["user"].summary == "Игрокам нравится"
        assert summaries["critic"].model == "fake/model" and summaries["user"].source_hash
        run = s.get_one(Run, created[-1])
        assert run.details["summaries"] == {"needed": 1, "done": 1, "failed": 0}
        s.execute(delete(RunItem).where(RunItem.day == TEST_DAY))
        s.execute(delete(CrawlState).where(CrawlState.day == TEST_DAY))

    created.append(await runner.run_once("test", client=client, llm=llm))
    assert llm.calls == 1  # отзывы не изменились — LLM повторно не вызывается


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


async def test_daily_recheck_refetches_young_unscored_games_once(isolated_day):
    tag, created = isolated_day
    young, old, scored = f"test-{tag}-young", f"test-{tag}-old", f"test-{tag}-scored"
    with session_scope(serverless=False) as s:
        s.add(Game(slug=young, title="Young", release_date=TEST_DAY - dt.timedelta(days=5)))
        s.add(Game(slug=old, title="Old", release_date=TEST_DAY - dt.timedelta(days=45)))
        s.add(Game(slug=scored, title="Scored", release_date=TEST_DAY - dt.timedelta(days=5), metascore=80))
    client = FakeGameClient([], [])  # отдаёт карточку с Metascore 80 на PC
    run_id = await runner.recheck_backlog(100, client=client)
    created.append(run_id)
    assert await runner.recheck_backlog(100, client=client) is None  # второй раз за день — пропуск
    with session_scope(serverless=False) as s:
        run = s.get_one(Run, run_id)
        assert run.phase == "recheck" and run.games_found == 1 and run.details["newly_scored"] == [young]
        assert s.scalars(select(Game.metascore).where(Game.slug == young)).one() == 80
        assert s.scalars(select(Game.metascore).where(Game.slug == old)).one() is None  # старше 30 дней
        items = s.scalars(select(RunItem).where(RunItem.run_id == run_id)).all()
        assert [(i.game_slug, i.source) for i in items] == [(young, "recheck")]
