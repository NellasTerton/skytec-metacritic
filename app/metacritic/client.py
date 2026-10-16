"""Асинхронный клиент внутреннего API backend.metacritic.com.

Подробности и подводные камни — docs/metacritic-research.md.
"""

import asyncio
import logging
from types import TracebackType
from typing import Any, Literal, Self

import httpx

from app.metacritic import parse
from app.metacritic.schemas import GameDetails, GameListItem, Page, Review, ReviewKind, ScoreStats

log = logging.getLogger(__name__)

BASE_URL = "https://backend.metacritic.com"
# С дефолтными UA библиотек (Python-urllib, python-requests) Cloudflare отвечает 403.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
GAME_TYPE_ID = 13
FINDER_MAX_LIMIT = 50  # на 51 уже 400
REVIEWS_MAX_LIMIT: dict[ReviewKind, int] = {"critic": 10, "user": 50}  # больше API молча не отдаёт

Sentiment = Literal["all", "positive", "neutral", "negative"]
ReviewSort = Literal["date", "score", "publication"]


class MetacriticError(Exception):
    pass


class NotFound(MetacriticError):
    pass


class MetacriticClient:
    def __init__(
        self,
        *,
        concurrency: int = 5,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._sem = asyncio.Semaphore(concurrency)
        self._retries = retries
        self._backoff = backoff

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """GET с ретраями на сетевые ошибки, 429 и 5xx."""
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with self._sem:
                    response = await self._http.get(path, params=params)
            except httpx.TransportError as e:
                last_error = MetacriticError(f"{type(e).__name__} on {path}: {e}")
            else:
                status = response.status_code
                if status == 404:
                    raise NotFound(path)
                if status == 429 or status >= 500:
                    last_error = MetacriticError(f"HTTP {status} on {path}")
                elif status >= 400:
                    raise MetacriticError(f"HTTP {status} on {path}: {response.text[:200]}")
                else:
                    return response.json()
            if attempt < self._retries:
                delay = self._backoff * 2**attempt
                log.warning("metacritic retry %d/%d in %.1fs: %s", attempt + 1, self._retries, delay, last_error)
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    # --- списки игр -------------------------------------------------------------------------

    async def _finder(self, *, offset: int, limit: int, **filters: Any) -> Page[GameListItem]:
        limit = min(limit, FINDER_MAX_LIMIT)
        params = {"mcoTypeId": GAME_TYPE_ID, "sortBy": "-releaseDate", "offset": offset, "limit": limit, **filters}
        data = await self.get_json("/finder/metacritic/web", params)
        return parse.parse_finder(data, offset=offset, limit=limit)

    async def new_releases(self, limit: int = 20) -> list[GameListItem]:
        """Блок New Releases на /game/ — только игры с Metascore."""
        return (await self._finder(offset=0, limit=limit, metaScoreMin=1)).items

    async def scored_newest(self, offset: int, limit: int = FINDER_MAX_LIMIT) -> Page[GameListItem]:
        """Как New Releases (только с Metascore, новые сверху), но любая страница, а не первые 20."""
        return await self._finder(offset=offset, limit=limit, metaScoreMin=1)

    async def browse_newest(self, offset: int = 0, limit: int = FINDER_MAX_LIMIT) -> Page[GameListItem]:
        """SEE ALL → Newest. Внимание: внутри одной даты порядок нестабилен, страницы пересекаются."""
        return await self._finder(offset=offset, limit=limit)

    # --- игра -------------------------------------------------------------------------------

    async def game(self, slug: str, *, platform_userscores: bool = True) -> GameDetails:
        data = await self.get_json(f"/composer/metacritic/pages/games/{slug}/web")
        if parse.composer_product_status(data) == 404:
            raise NotFound(f"game {slug}")
        game = parse.parse_composer(data)
        if platform_userscores:
            await self._fill_platform_userscores(game)
        return game

    async def _fill_platform_userscores(self, game: GameDetails) -> None:
        others = [p for p in game.platforms if not p.is_lead]
        results = await asyncio.gather(
            *(self.platform_stats(game.slug, p.slug, "user") for p in others), return_exceptions=True
        )
        for platform, result in zip(others, results, strict=True):
            if isinstance(result, ScoreStats):
                platform.userscore = result.score
                platform.user_review_count = result.review_count
            else:
                log.warning("userscore for %s/%s failed: %s", game.slug, platform.slug, result)

    async def platform_stats(self, slug: str, platform: str, kind: ReviewKind) -> ScoreStats:
        # Платформа задаётся только в пути: query-параметр ?platform= API игнорирует.
        data = await self.get_json(f"/reviews/metacritic/{kind}/games/{slug}/platform/{platform}/stats/web")
        return parse.parse_stats_response(data)

    async def reviews(
        self,
        slug: str,
        platform: str,
        kind: ReviewKind,
        *,
        offset: int = 0,
        limit: int | None = None,
        sentiment: Sentiment = "all",
        sort: ReviewSort = "date",
    ) -> Page[Review]:
        max_limit = REVIEWS_MAX_LIMIT[kind]
        limit = min(limit or max_limit, max_limit)
        params = {"offset": offset, "limit": limit, "filterBySentiment": sentiment, "sort": sort}
        data = await self.get_json(f"/reviews/metacritic/{kind}/games/{slug}/platform/{platform}/web", params)
        return parse.parse_reviews(data, kind, offset=offset, limit=limit)
