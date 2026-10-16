"""Сайт. Vercel находит `app` в app/main.py автоматически."""

import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app import queries
from app.config import get_settings
from app.db import DatabaseNotConfigured, session_scope
from app.github import DispatchError, dispatch_worker, latest_worker_run
from app.platforms import short_platform
from app.telegram import handle_update

logging.basicConfig(level=get_settings().log_level)
# httpx на INFO пишет полный URL каждого запроса, а в URL бывают секреты (токен бота в пути
# api.telegram.org/bot<токен>/…), поэтому только предупреждения и ошибки.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

app = FastAPI(title="Metacritic Games", docs_url="/api/docs", openapi_url="/api/openapi.json")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["short_platform"] = short_platform
templates.env.filters["plural"] = queries.plural
templates.env.filters["views"] = queries.format_views
templates.env.filters["duration"] = queries.format_duration
templates.env.filters["released_ago"] = queries.released_ago
templates.env.globals["page_window"] = queries.page_window
templates.env.globals["page_size"] = queries.PAGE_SIZE
templates.env.globals["fresh_step"] = queries.FRESH_STEP
templates.env.globals["fresh_max"] = queries.FRESH_MAX
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
    sort: str = queries.DEFAULT_SORT,
    page: int = 1,
    fresh: int = queries.FRESH_STEP,
    partial: int = 0,
) -> HTMLResponse:
    if sort not in queries.SORTS:
        sort = queries.DEFAULT_SORT
    fresh = min(max(fresh, queries.FRESH_STEP), queries.FRESH_MAX)
    today = datetime.now(ZoneInfo(get_settings().service_tz)).date()
    context = {
        "nav": "games",
        "q": q,
        "platform": platform,
        "sort": sort,
        "sorts": queries.SORTS,
        "today": today,
        "fresh_limit": fresh,
    }

    def page_url(n: int, fresh_limit: int = fresh) -> str:
        params = {"q": q, "platform": platform, "sort": "" if sort == queries.DEFAULT_SORT else sort, "page": n}
        if fresh_limit != queries.FRESH_STEP:
            params["fresh"] = fresh_limit
        return "/?" + urlencode({k: v for k, v in params.items() if v and not (k == "page" and v == 1)})

    try:
        with session_scope() as session:
            context["result"] = queries.list_games(session, q=q, platform=platform, sort=sort, page=page)
            context["fresh"], context["fresh_total"] = queries.fresh_releases(
                session, today=today, q=q, platform=platform, limit=fresh
            )
            context["platforms"] = queries.platform_options(session)
            if not partial:
                context["hero_image"] = queries.hero_image(session)
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


# --- Telegram ---------------------------------------------------------------------------


@app.post("/api/telegram/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
) -> JSONResponse:
    """Апдейты бота. Всегда 200 после проверки секрета: иначе Telegram будет слать апдейт повторно."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_webhook_secret:
        return JSONResponse({"error": "telegram is not configured"}, status_code=503)
    if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", settings.telegram_webhook_secret):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        action = await handle_update(await request.json(), settings.telegram_bot_token, settings.site_url)
    except Exception:
        log.exception("telegram update failed")
        action = "error"
    return JSONResponse({"ok": True, "action": action})


# --- мониторинг -------------------------------------------------------------------------


def _day_start() -> datetime:
    tz = ZoneInfo(get_settings().service_tz)
    return datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def _monitor_data() -> dict:
    with session_scope() as session:
        data = queries.monitor_status(session, day_start=_day_start())
        running = queries.active_run(session)
        data["active_run_id"] = running.id if running else None
    return data


def _active_run_id() -> int | None:
    with session_scope() as session:
        running = queries.active_run(session)
        return running.id if running else None


def _run_blocker(github: dict | None, active_run_id: int | None) -> str | None:
    if github and github["active"]:
        return "воркер уже в очереди или работает в GitHub Actions"
    if active_run_id:
        return f"идёт прогон #{active_run_id}"
    return None


@app.get("/api/status")
async def status() -> JSONResponse:
    settings = get_settings()
    try:
        data = await run_in_threadpool(_monitor_data)
    except DatabaseNotConfigured:
        return JSONResponse({"error": "database is not configured"}, status_code=503)
    github = await latest_worker_run(settings)
    blocker = _run_blocker(github, data.pop("active_run_id"))
    data.update(
        github=github,
        dispatch_configured=bool(settings.github_dispatch_token),
        can_run=bool(settings.github_dispatch_token) and blocker is None,
        run_blocker=blocker,
    )
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@app.post("/api/run")
async def run_now() -> JSONResponse:
    """Кнопка «Запустить сейчас»: workflow_dispatch, если воркер сейчас не занят."""
    settings = get_settings()
    if not settings.github_dispatch_token:
        return JSONResponse({"error": "GITHUB_DISPATCH_TOKEN is not configured"}, status_code=503)
    github = await latest_worker_run(settings, use_cache=False)
    active = await run_in_threadpool(_active_run_id)
    if blocker := _run_blocker(github, active):
        return JSONResponse({"dispatched": False, "reason": blocker}, status_code=409)
    try:
        await dispatch_worker(settings, "web")
    except DispatchError as e:
        log.error("manual dispatch failed: %s", e)
        return JSONResponse({"dispatched": False, "reason": str(e)}, status_code=502)
    log.info("manual run dispatched from the website")
    return JSONResponse({"dispatched": True})


@app.get("/monitor", response_class=HTMLResponse)
def monitor(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "monitor.html", {"nav": "monitor"})
