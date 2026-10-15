"""Воркер в GitHub Actions: запуск через workflow_dispatch и статус последнего запуска."""

import time

import httpx

from app.config import Settings

API_URL = "https://api.github.com"
ACTIVE_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}
STATUS_CACHE_SECONDS = 10.0

_status_cache: dict[str, tuple[float, dict | None]] = {}


class DispatchError(RuntimeError):
    pass


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_dispatch_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _workflow_url(settings: Settings) -> str:
    return f"{API_URL}/repos/{settings.github_repository}/actions/workflows/{settings.github_workflow}"


async def dispatch_worker(
    settings: Settings, trigger: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    if not settings.github_dispatch_token:
        raise DispatchError("GITHUB_DISPATCH_TOKEN is not set")
    body = {"ref": settings.github_ref, "inputs": {"trigger": trigger}}
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        response = await client.post(f"{_workflow_url(settings)}/dispatches", json=body, headers=_headers(settings))
    if response.status_code != 204:
        raise DispatchError(f"GitHub API HTTP {response.status_code}: {response.text[:300]}")
    _status_cache.clear()  # следующий опрос статуса сразу увидит новый запуск


async def latest_worker_run(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None, use_cache: bool = True
) -> dict | None:
    """Последний запуск workflow воркера в Actions. Кэшируется: страница мониторинга опрашивает часто."""
    if not settings.github_dispatch_token:
        return None
    cached = _status_cache.get(settings.github_workflow)
    if use_cache and cached and time.monotonic() - cached[0] < STATUS_CACHE_SECONDS:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            response = await client.get(
                f"{_workflow_url(settings)}/runs", params={"per_page": 1}, headers=_headers(settings)
            )
        response.raise_for_status()
        runs = response.json().get("workflow_runs") or []
    except httpx.HTTPError:
        return None
    result = None
    if runs:
        run = runs[0]
        result = {
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "event": run.get("event"),
            "created_at": run.get("created_at"),
            "url": run.get("html_url"),
            "active": run.get("status") in ACTIVE_STATUSES,
        }
    _status_cache[settings.github_workflow] = (time.monotonic(), result)
    return result
