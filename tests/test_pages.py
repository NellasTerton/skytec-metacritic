"""Рендер страниц без БД: запросы подменены, проверяется сама вёрстка (фильтры, пагинация, метки)."""

import datetime as dt
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app import main, queries
from app.models import Game, GamePlatform


def make_game(i: int) -> Game:
    return Game(
        slug=f"game-{i}",
        title=f"Game {i}",
        release_date=dt.date(2026, 10, 15),
        developers=["Studio"],
        publishers=["Publisher"],
        genres=["RPG"],
        metascore=80,
        userscore=7.5,
        cover_url=None,
        platforms=[
            GamePlatform(slug="pc", name="PC", is_lead=True, metascore=80, userscore=7.5),
            GamePlatform(slug="playstation-5", name="PlayStation 5", metascore=90, userscore=None),
        ],
    )


@pytest.fixture
def fake_db(monkeypatch):
    @contextmanager
    def scope(*args, **kwargs):
        yield None

    games = [make_game(i) for i in range(queries.PAGE_SIZE)]
    monkeypatch.setattr(main, "session_scope", scope)
    monkeypatch.setattr(
        queries, "list_games", lambda s, **kw: queries.GameList(games=games, total=121, page=kw["page"], pages=6)
    )
    monkeypatch.setattr(queries, "platform_options", lambda s, **kw: [("playstation-5", "PlayStation 5", 21)])
    monkeypatch.setattr(queries, "hero_image", lambda s: "https://example.test/banner.jpg")


def test_list_page_renders(fake_db):
    html = TestClient(main.app).get("/", params={"page": 3}).text
    assert "121 игра с оценками" in html
    assert "Показано 49–72 из 121" in html
    assert 'aria-current="page">3<' in html and ">6</a>" in html and "…" in html
    assert "banner.jpg" in html and "Critics" in html and "Users" in html


def test_platform_filter_highlights_platform(fake_db):
    html = TestClient(main.app).get("/", params={"platform": "playstation-5", "partial": 1}).text
    assert '<span class="tag active" title="PlayStation 5">PS5</span>' in html
    assert "<h1>" not in html  # partial — только блок результатов
    assert ">90<" in html  # оценка выбранной платформы, а не основной (80)


def test_game_card_renders_letsplay(monkeypatch):
    from app.models import Letsplay

    @contextmanager
    def scope(*args, **kwargs):
        yield None

    game = make_game(1)
    game.summaries = []
    game.media = {}
    game.letsplay = Letsplay(
        status="done",
        video_id="wiM8ZLMHIYM",
        video_title="I've Never Played Anything Like This (SILVER PINES)",
        channel="theRadBrad",
        view_count=443990,
        duration_s=5465,
        method="captions",
        transcript="All right so I've played a lot of horror games",
        retelling="Блогер в восторге.",
        verdict="Игра понравилась.",
        pros=["атмосфера"],
        cons=["перезарядка"],
        liked=True,
    )
    monkeypatch.setattr(main, "session_scope", scope)
    monkeypatch.setattr(queries, "get_game", lambda s, slug: game)
    monkeypatch.setattr(queries, "similar_games", lambda s, g: [])
    html = TestClient(main.app).get("/game/game-1").text
    assert "<h2>Летсплей</h2>" in html and "theRadBrad" in html and "444 тыс. просмотров" in html
    assert "1 ч 31 мин" in html and "Блогеру понравилось" in html
    assert "Расшифровка речи блогера" in html and "horror games" in html
    assert "youtube-nocookie.com/embed/wiM8ZLMHIYM" in html
