"""Сайт. Vercel находит `app` в app/main.py автоматически."""

import hmac
import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app import queries
from app.config import get_settings
from app.db import DatabaseNotConfigured, session_scope
from app.github import DispatchError, dispatch_worker
from app.platforms import short_platform

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger(__name__)

app = FastAPI(title="Metacritic Games", docs_url="/api/docs", openapi_url="/api/openapi.json")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["short_platform"] = short_platform
templates.env.filters["initials"] = lambda title: "".join(w[0] for w in (title or "?").split()[:2]).upper()


@app.get("/api/health")
def health() -> JSONResponse:
    try:
        with session_scope() as session:
            session.execute(text("select 1"))
            revision = session.execute(text("select version_num from alembic_version")).scalar()
    except DatabaseNotConfigured:
        return JSONResponse({"status": "degraded", "db": "not_configured"}, status_code=503)
    except Exception as e:
        log.exception("health check failed")
        return JSONResponse({"status": "degraded", "db": f"error: {type(e).__name__}"}, status_code=503)
    return JSONResponse({"status": "ok", "db": "ok", "migration": revision})


def _auto_run_blocker() -> str | None:
    with session_scope() as session:
        return queries.auto_run_blocker(session, get_settings().auto_run_min_interval_minutes)


@app.api_route("/api/cron", methods=["GET", "POST"], include_in_schema=False)
async def cron(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Ежечасный запуск воркера: внешний cron → сюда → workflow_dispatch в GitHub Actions."""
    settings = get_settings()
    if not settings.cron_secret or not settings.github_dispatch_token:
        return JSONResponse({"error": "CRON_SECRET or GITHUB_DISPATCH_TOKEN is not configured"}, status_code=503)
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {settings.cron_secret}"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    blocker = await run_in_threadpool(_auto_run_blocker)
    if blocker:
        log.info("cron: dispatch skipped: %s", blocker)
        return JSONResponse({"dispatched": False, "reason": blocker})
    try:
        await dispatch_worker(settings, "cron")
    except DispatchError as e:
        log.error("cron: dispatch failed: %s", e)
        return JSONResponse({"dispatched": False, "error": str(e)}, status_code=502)
    log.info("cron: worker dispatched")
    return JSONResponse({"dispatched": True})


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    q: str = "",
    platform: str = "",
    sort: str = "metascore",
    page: int = 1,
    all: int = 0,
    partial: int = 0,
) -> HTMLResponse:
    if sort not in queries.SORTS:
        sort = "metascore"
    scored_only = not all
    context = {
        "nav": "games",
        "q": q,
        "platform": platform,
        "sort": sort,
        "sorts": queries.SORTS,
        "scored_only": scored_only,
    }

    def page_url(n: int) -> str:
        params = {"q": q, "platform": platform, "sort": sort, "all": all, "page": n}
        return "/?" + urlencode({k: v for k, v in params.items() if v})

    try:
        with session_scope() as session:
            context["result"] = queries.list_games(
                session, q=q, platform=platform, sort=sort, scored_only=scored_only, page=page
            )
            context["platforms"] = queries.platform_options(session, scored_only=scored_only)
    except DatabaseNotConfigured:
        return templates.TemplateResponse(request, "index.html", {**context, "configured": False})
    # partial=1 — только блок результатов, для поиска при вводе без перезагрузки страницы.
    template = "_results.html" if partial else "index.html"
    return templates.TemplateResponse(request, template, {**context, "configured": True, "page_url": page_url})


@app.get("/game/{slug}", response_class=HTMLResponse)
def game_card(request: Request, slug: str) -> HTMLResponse:
    with session_scope() as session:
        game = queries.get_game(session, slug)
        if game is None:
            raise HTTPException(status_code=404, detail="Игра не найдена")
        similar = queries.similar_games(session, game)
        summaries = {s.kind: s for s in game.summaries}
        return templates.TemplateResponse(
            request, "game.html", {"nav": "games", "game": game, "similar": similar, "summaries": summaries}
        )
