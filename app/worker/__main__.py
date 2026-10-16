"""CLI воркера.

python -m app.worker run --trigger schedule    прогон (пишет в БД)
python -m app.worker summarize --limit 20      резюме для игр в БД, у которых их ещё нет
python -m app.worker media --limit 40          трейлеры и скриншоты для игр в БД без них
python -m app.worker letsplays --limit 3       летсплеи для игр с оценками (YouTube Data API + Gemini)
python -m app.worker recheck [--force]         раз в сутки: игры младше 30 дней без оценок — заново
python -m app.worker embeddings --limit 300    эмбеддинги описаний для похожих игр (Gemini)
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
    media_p = sub.add_parser("media", help="trailers and screenshots for games in DB without them")
    media_p.add_argument("--limit", type=int, default=40)
    lp_p = sub.add_parser("letsplays", help="let's play verdicts for scored games")
    lp_p.add_argument("--limit", type=int, default=3)
    rc_p = sub.add_parser("recheck", help="daily: refetch unscored games younger than 30 days")
    rc_p.add_argument("--limit", type=int, default=5000)
    rc_p.add_argument("--force", action="store_true", help="run even if already done today")
    emb_p = sub.add_parser("embeddings", help="description embeddings for similar games")
    emb_p.add_argument("--limit", type=int, default=300)
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
    elif args.command == "media":
        from app.worker.runner import media_backlog

        asyncio.run(media_backlog(args.limit))
    elif args.command == "embeddings":
        from app.worker.runner import embeddings_backlog

        asyncio.run(embeddings_backlog(args.limit))
    elif args.command == "recheck":
        from app.worker.runner import recheck_backlog

        asyncio.run(recheck_backlog(args.limit, force=args.force))
    elif args.command == "letsplays":
        from app.worker.runner import letsplay_backlog

        asyncio.run(letsplay_backlog(args.limit))
    elif args.command == "game":
        asyncio.run(_print_game(args.slug))
    elif args.command == "new-releases":
        asyncio.run(_print_new_releases())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
