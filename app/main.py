"""Сайт. Vercel находит `app` в app/main.py автоматически."""

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app import queries
from app.config import get_settings
from app.db import DatabaseNotConfigured, session_scope

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger(__name__)

app = FastAPI(title="Metacritic Games", docs_url="/api/docs", openapi_url="/api/openapi.json")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = "", platform: str = "", sort: str = "metascore", page: int = 1) -> HTMLResponse:
    if sort not in queries.SORTS:
        sort = "metascore"
    context = {"nav": "games", "q": q, "platform": platform, "sort": sort, "sorts": queries.SORTS}

    def page_url(n: int) -> str:
        params = {k: v for k, v in {"q": q, "platform": platform, "sort": sort, "page": n}.items() if v}
        return "/?" + urlencode(params)

    try:
        with session_scope() as session:
            context["result"] = queries.list_games(session, q=q, platform=platform, sort=sort, page=page)
            context["platforms"] = queries.platform_options(session)
    except DatabaseNotConfigured:
        return templates.TemplateResponse(request, "index.html", {**context, "configured": False})
    return templates.TemplateResponse(request, "index.html", {**context, "configured": True, "page_url": page_url})


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
