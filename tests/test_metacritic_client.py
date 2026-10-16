import httpx
import pytest

from app.metacritic import MetacriticClient, MetacriticError, NotFound
from app.metacritic.client import BROWSER_UA
from tests.conftest import fixture_transport, load


def make_client(transport, **kwargs) -> MetacriticClient:
    return MetacriticClient(transport=transport, backoff=0, **kwargs)


async def test_new_releases_and_browse(transport):
    async with make_client(transport) as c:
        items = await c.new_releases()
        page = await c.browse_newest(offset=0, limit=500)
    assert len(items) == 20
    assert page.limit == 50  # клиент режет limit до максимума API
    finder_calls = [r for r in transport.calls if r.url.path == "/finder/metacritic/web"]
    assert finder_calls[0].url.params["metaScoreMin"] == "1"
    assert finder_calls[1].url.params["limit"] == "50"
    assert all(r.headers["user-agent"] == BROWSER_UA for r in transport.calls)


async def test_scored_newest_pages_past_new_releases(transport):
    async with make_client(transport) as c:
        await c.scored_newest(offset=20)
    call = next(r for r in transport.calls if r.url.path == "/finder/metacritic/web")
    assert call.url.params["metaScoreMin"] == "1" and call.url.params["offset"] == "20"
    assert call.url.params["sortBy"] == "-releaseDate" and call.url.params["limit"] == "50"


async def test_game_fills_userscores_for_all_platforms(transport):
    async with make_client(transport) as c:
        game = await c.game("elden-ring")
    scores = {p.slug: p.userscore for p in game.platforms}
    assert scores["playstation-5"] == pytest.approx(8.4)  # из composer
    assert scores["pc"] == pytest.approx(7.6)  # дозапрошено
    assert scores["xbox-series-x"] is not None
    stats_paths = {r.url.path for r in transport.calls if r.url.path.endswith("/stats/web")}
    assert "/reviews/metacritic/user/games/elden-ring/platform/pc/stats/web" in stats_paths
    assert not any("platform/playstation-5" in p for p in stats_paths)  # lead уже есть


async def test_game_not_found_inside_200(transport):
    async with make_client(transport) as c:
        with pytest.raises(NotFound):
            await c.game("no-such-game-xyz-123")


async def test_platform_stats_failure_does_not_fail_game():
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    transport = fixture_transport({"/reviews/metacritic/user/games/elden-ring/platform/pc/stats/web": broken})
    async with make_client(transport) as c:
        game = await c.game("elden-ring")
    assert next(p for p in game.platforms if p.slug == "pc").userscore is None


async def test_reviews_limit_clamped(transport):
    async with make_client(transport) as c:
        critic = await c.reviews("elden-ring", "pc", "critic", limit=100, sort="score")
        user = await c.reviews("elden-ring", "pc", "user")
    assert critic.limit == 10 and user.limit == 50
    critic_call = next(r for r in transport.calls if "/critic/" in r.url.path)
    assert critic_call.url.params["limit"] == "10"


async def test_retries_on_5xx_then_succeeds():
    attempts = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=load("finder_new_releases"))

    async with make_client(fixture_transport({"/finder/metacritic/web": flaky}), retries=3) as c:
        items = await c.new_releases()
    assert len(items) == 20 and attempts["n"] == 3


async def test_gives_up_after_retries():
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    async with make_client(fixture_transport({"/finder/metacritic/web": down}), retries=2) as c:
        with pytest.raises(MetacriticError, match="HTTP 502"):
            await c.new_releases()


async def test_403_is_not_retried():
    attempts = {"n": 0}

    def forbidden(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(403, text="Just a moment...")

    async with make_client(fixture_transport({"/finder/metacritic/web": forbidden}), retries=3) as c:
        with pytest.raises(MetacriticError, match="HTTP 403"):
            await c.new_releases()
    assert attempts["n"] == 1
