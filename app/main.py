"""Точка входа сайта. Vercel находит `app` в app/main.py автоматически."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import DatabaseNotConfigured, session_scope
from app.models import Game, Run

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
def index(request: Request) -> HTMLResponse:
    stats: dict = {"configured": True}
    try:
        with session_scope() as session:
            stats["games"] = session.scalar(select(func.count()).select_from(Game))
            stats["last_run"] = session.scalars(select(Run).order_by(Run.started_at.desc()).limit(1)).first()
    except DatabaseNotConfigured:
        stats["configured"] = False
    return templates.TemplateResponse(request, "index.html", {"stats": stats})
