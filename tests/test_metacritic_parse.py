import datetime as dt

import pytest

from app.metacritic import parse
from tests.conftest import load


def test_finder_new_releases():
    page = parse.parse_finder(load("finder_new_releases"), offset=0, limit=20)
    assert page.total > 1000
    assert len(page.items) == 20
    assert all(item.metascore is not None for item in page.items)  # metaScoreMin=1
    first = page.items[0]
    assert first.slug and first.title and first.platform
    assert isinstance(first.release_date, dt.date)
    assert first.cover_url.startswith("https://www.metacritic.com/a/img/catalog/provider/")


def test_finder_error_raises():
    with pytest.raises(parse.ParseError):
        parse.parse_finder({"errors": [{"code": 400}]}, offset=0, limit=51)


def test_composer_rich_game():
    game = parse.parse_composer(load("composer_elden-ring"))
    assert game.title == "Elden Ring"
    assert game.url == "https://www.metacritic.com/game/elden-ring/"
    assert game.developers == ["From Software"]
    assert "Bandai Namco Games" in game.publishers
    assert game.genres == ["Action RPG"]
    assert game.description
    assert game.cover_url and game.banner_url
    assert game.video and game.video.embed_url.startswith("https://cdn.jwplayer.com/")

    assert game.platforms[0].is_lead and game.platforms[0].slug == "playstation-5"
    assert {p.slug for p in game.platforms} >= {"pc", "playstation-5", "xbox-series-x"}
    lead = game.lead_platform
    assert lead.metascore == 96 and lead.userscore == pytest.approx(8.4)
    # Userscore не-lead платформ composer не отдаёт — его дозапрашивает клиент.
    assert next(p for p in game.platforms if p.slug == "pc").userscore is None

    assert game.critic_score.score == 96 and game.user_score.max == 10
    assert game.critic_reviews_sample and game.user_reviews_sample
    assert {r.kind for r in game.user_reviews_sample} == {"user"}
    assert len(game.user_reviews_sample) == len({(r.author, r.quote) for r in game.user_reviews_sample})
    assert game.user_reviews_total > 1000


def test_composer_fresh_game_tbd_scores():
    game = parse.parse_composer(load("composer_enshrouded"))
    assert game.developers == ["Keen Games"]
    assert game.user_score.review_count == 0
    assert game.user_score.score is None  # 0 при нуле отзывов — это «tbd», а не ноль
    ps5 = next(p for p in game.platforms if p.slug == "playstation-5")
    assert ps5.metascore is None and ps5.critic_review_count == 2  # мало отзывов — score не публикуется


def test_composer_empty_game():
    game = parse.parse_composer(load("composer_robot-city-delivery"))
    assert game.video is None
    assert game.critic_score.score is None and game.user_score.score is None
    assert game.critic_reviews_sample == [] and game.user_reviews_sample == []
    assert game.cover_url


def test_composer_not_found_status():
    assert parse.composer_product_status(load("composer_not_found")) == 404
    assert parse.composer_product_status(load("composer_elden-ring")) == 200
    with pytest.raises(parse.ParseError):
        parse.parse_composer(load("composer_not_found"))


def test_score_stats_normalization():
    stats = parse.parse_score_stats({"max": 10, "score": 0, "reviewCount": 0})
    assert stats.score is None
    stats = parse.parse_stats_response(load("user_stats_elden-ring_pc"))
    assert stats.max == 10 and stats.score == pytest.approx(7.6) and stats.review_count > 1000


def test_reviews_pages():
    critic = parse.parse_reviews(load("reviews_critic_elden-ring_pc"), "critic", offset=0, limit=10)
    assert critic.total > 10 and len(critic.items) == 10
    assert critic.items[0].publication and critic.items[0].url.startswith("http")
    user = parse.parse_reviews(load("reviews_user_elden-ring_pc"), "user", offset=0, limit=50)
    assert len(user.items) == 50 and user.items[0].publication is None
    assert all(0 <= r.score <= 10 for r in user.items)


def test_image_url():
    assert parse.image_url({"bucketPath": "/provider/7/2/x.jpg"}) == (
        "https://www.metacritic.com/a/img/catalog/provider/7/2/x.jpg"
    )
    assert parse.image_url({"bucketPath": None}) is None
    assert parse.image_url(None) is None
