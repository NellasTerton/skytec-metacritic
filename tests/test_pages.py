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
    fresh = make_game(99)
    fresh.slug, fresh.metascore, fresh.userscore = "fresh-one", None, None
    fresh.release_date = dt.date(2026, 10, 10)
    calls = []

    def fake_fresh(s, **kw):
        calls.append(kw)
        return [fresh], 30

    monkeypatch.setattr(queries, "fresh_releases", fake_fresh)
    return calls


def test_list_page_renders(fake_db):
    html = TestClient(main.app).get("/", params={"page": 3}).text
    assert "121 игра с оценками" in html
    assert "Показано 49–72 из 121" in html
    assert 'aria-current="page">3<' in html and ">6</a>" in html and "…" in html
    assert "banner.jpg" in html and "Critics" in html and "Users" in html
    assert "Только с оценками" not in html


def test_fresh_releases_block_below_scored(fake_db):
    html = TestClient(main.app).get("/", params={"q": "x"}).text
    assert html.index("121 игра с оценками") < html.index("Свежие релизы, оценок пока нет")
    assert "30 игр" in html and 'href="/game/fresh-one"' in html
    assert "вышла " in html and "назад" in html
    # «Показать ещё» сохраняет поиск и увеличивает блок на шаг.
    assert 'href="/?q=x&amp;fresh=48"' in html
    assert fake_db[-1]["q"] == "x" and fake_db[-1]["limit"] == queries.FRESH_STEP


def test_default_sort_is_newest_first(fake_db):
    html = TestClient(main.app).get("/").text
    assert '<option value="release" selected>Сначала новые</option>' in html
    # Сортировка по умолчанию не попадает в ссылки, выбранная — попадает.
    assert "sort=release" not in html
    assert "sort=metascore" in TestClient(main.app).get("/", params={"sort": "metascore"}).text


def test_released_ago():
    today = dt.date(2026, 10, 16)
    assert queries.released_ago(today, today) == "вышла сегодня"
    assert queries.released_ago(dt.date(2026, 10, 15), today) == "вышла вчера"
    assert queries.released_ago(dt.date(2026, 10, 13), today) == "вышла 3 дня назад"
    assert queries.released_ago(dt.date(2026, 10, 5), today) == "вышла 11 дней назад"
    assert queries.released_ago(dt.date(2026, 9, 25), today) == "вышла 21 день назад"
    assert queries.released_ago(None, today) == "дата выхода неизвестна"


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
        quotes=[
            {
                "text": "It's like if Silent Hill was a Metroidvania game.",
                "ru": "Это как Silent Hill в жанре метроидвании.",
            }
        ],
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
    assert "Цитаты блогера" in html and "Silent Hill was a Metroidvania" in html
    assert "Это как Silent Hill в жанре метроидвании." in html
    # Режим «по видео»: полной расшифровки нет — спойлер не показываем.
    game.letsplay.method, game.letsplay.transcript = "video", None
    html = TestClient(main.app).get("/game/game-1").text
    assert "Расшифровка речи блогера" not in html and "Цитаты блогера" in html


def test_game_card_shows_metacritic_video_first(monkeypatch):
    @contextmanager
    def scope(*args, **kwargs):
        yield None

    game = make_game(1)
    game.summaries, game.letsplay = [], None
    # Медиа, собранное раньше: YouTube был первым.
    game.media = {
        "videos": [
            {"source": "youtube", "title": "YT", "embed_url": "https://www.youtube.com/embed/y", "url": "y"},
            {"source": "metacritic", "title": "MC", "embed_url": "https://cdn.jwplayer.com/players/x.html", "url": "x"},
        ]
    }
    monkeypatch.setattr(main, "session_scope", scope)
    monkeypatch.setattr(queries, "get_game", lambda s, slug: game)
    monkeypatch.setattr(queries, "similar_games", lambda s, g: [])
    html = TestClient(main.app).get("/game/game-1").text
    assert 'class="poster" type="button" data-embed="https://cdn.jwplayer.com/players/x.html"' in html
    assert html.index(">Metacritic</button>") < html.index(">YouTube</button>")
