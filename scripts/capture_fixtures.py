"""Снимает реальные ответы Metacritic в tests/fixtures/metacritic/ для офлайн-тестов.

python -m scripts.capture_fixtures   (из корня репозитория)
"""

import asyncio
import json
from pathlib import Path

from app.metacritic.client import GAME_TYPE_ID, MetacriticClient

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "metacritic"
# Компоненты composer, которые нам не нужны и только раздувают фикстуры.
DROP_COMPONENTS = {"related-news", "related-carousel", "where-to-buy", "game-stats"}

GAMES = {
    "elden-ring": "много платформ и отзывов",
    "enshrouded": "свежая игра: userscore tbd, у части платформ нет metascore",
    "robot-city-delivery": "пустая игра из SEE ALL: без оценок, отзывов и видео",
}


def save(name: str, data: dict) -> None:
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{path.relative_to(OUT.parent.parent.parent)}  {path.stat().st_size // 1024} KB")


def strip_composer(data: dict) -> dict:
    data = {k: v for k, v in data.items() if k not in {"header", "footer"}}
    data["components"] = [
        c for c in data["components"] if c.get("meta", {}).get("componentName") not in DROP_COMPONENTS
    ]
    return data


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with MetacriticClient() as c:
        finder = "/finder/metacritic/web"
        save(
            "finder_new_releases",
            await c.get_json(
                finder,
                {"mcoTypeId": GAME_TYPE_ID, "sortBy": "-releaseDate", "metaScoreMin": 1, "offset": 0, "limit": 20},
            ),
        )
        save(
            "finder_browse_newest",
            await c.get_json(finder, {"mcoTypeId": GAME_TYPE_ID, "sortBy": "-releaseDate", "offset": 0, "limit": 10}),
        )

        for slug in GAMES:
            save(f"composer_{slug}", strip_composer(await c.get_json(f"/composer/metacritic/pages/games/{slug}/web")))
        save(
            "composer_not_found",
            strip_composer(await c.get_json("/composer/metacritic/pages/games/no-such-game-xyz-123/web")),
        )

        for platform in ("pc", "xbox-series-x", "playstation-4", "xbox-one"):
            save(
                f"user_stats_elden-ring_{platform}",
                await c.get_json(f"/reviews/metacritic/user/games/elden-ring/platform/{platform}/stats/web"),
            )
        for platform in ("xbox-series-x", "playstation-5"):
            save(
                f"user_stats_enshrouded_{platform}",
                await c.get_json(f"/reviews/metacritic/user/games/enshrouded/platform/{platform}/stats/web"),
            )

        save(
            "reviews_critic_elden-ring_pc",
            await c.get_json(
                "/reviews/metacritic/critic/games/elden-ring/platform/pc/web",
                {"offset": 0, "limit": 10, "filterBySentiment": "all", "sort": "score"},
            ),
        )
        save(
            "reviews_user_elden-ring_pc",
            await c.get_json(
                "/reviews/metacritic/user/games/elden-ring/platform/pc/web",
                {"offset": 0, "limit": 50, "filterBySentiment": "all", "sort": "date"},
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
