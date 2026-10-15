"""Запуск воркера в GitHub Actions через workflow_dispatch."""

import httpx

from app.config import Settings

API_URL = "https://api.github.com"


class DispatchError(RuntimeError):
    pass


async def dispatch_worker(
    settings: Settings, trigger: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    if not settings.github_dispatch_token:
        raise DispatchError("GITHUB_DISPATCH_TOKEN is not set")
    url = f"{API_URL}/repos/{settings.github_repository}/actions/workflows/{settings.github_workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {settings.github_dispatch_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = {"ref": settings.github_ref, "inputs": {"trigger": trigger}}
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        response = await client.post(url, json=body, headers=headers)
    if response.status_code != 204:
        raise DispatchError(f"GitHub API HTTP {response.status_code}: {response.text[:300]}")
