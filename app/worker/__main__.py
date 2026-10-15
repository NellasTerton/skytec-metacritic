"""CLI воркера.

python -m app.worker run --trigger schedule    прогон (пишет в БД)
python -m app.worker game elden-ring           карточка игры в JSON, без БД
python -m app.worker new-releases              список New Releases, без БД
"""

import argparse
import asyncio
import json
import logging
import sys

from app.config import get_settings
from app.metacritic import MetacriticClient


async def _print_game(slug: str) -> None:
    async with MetacriticClient() as client:
        game = await client.game(slug)
    print(game.model_dump_json(indent=2))


async def _print_new_releases() -> None:
    async with MetacriticClient() as client:
        items = await client.new_releases()
    print(json.dumps([i.model_dump(mode="json", exclude={"description"}) for i in items], ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="one worker run")
    run_p.add_argument("--trigger", default="manual")
    game_p = sub.add_parser("game", help="print parsed game as JSON")
    game_p.add_argument("slug")
    sub.add_parser("new-releases", help="print New Releases")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "run":
        from app.worker.runner import run_once

        asyncio.run(run_once(args.trigger))
    elif args.command == "game":
        asyncio.run(_print_game(args.slug))
    elif args.command == "new-releases":
        asyncio.run(_print_new_releases())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
