"""CLI воркера.

python -m app.worker run --trigger schedule    прогон (пишет в БД)
python -m app.worker summarize --limit 20      резюме для игр в БД, у которых их ещё нет
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
from app.queries import AUTO_TRIGGERS


def _auto_run_blocker() -> str | None:
    from app.db import session_scope
    from app.queries import auto_run_blocker

    with session_scope(serverless=False) as session:
        return auto_run_blocker(session, get_settings().auto_run_min_interval_minutes)


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
    sum_p = sub.add_parser("summarize", help="summaries for games in DB that have none")
    sum_p.add_argument("--limit", type=int, default=20)
    game_p = sub.add_parser("game", help="print parsed game as JSON")
    game_p.add_argument("slug")
    sub.add_parser("new-releases", help="print New Releases")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "run":
        from app.worker.runner import run_once

        if args.trigger in AUTO_TRIGGERS and (reason := _auto_run_blocker()):
            # Внешний cron и запасной cron GitHub: в час выполняется только первый из них.
            logging.getLogger("app.worker").info("automatic run skipped: %s", reason)
            return 0
        asyncio.run(run_once(args.trigger))
    elif args.command == "summarize":
        from app.worker.runner import summarize_backlog

        asyncio.run(summarize_backlog(args.limit))
    elif args.command == "game":
        asyncio.run(_print_game(args.slug))
    elif args.command == "new-releases":
        asyncio.run(_print_new_releases())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
