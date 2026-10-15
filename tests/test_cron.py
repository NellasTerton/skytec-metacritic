import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.github import DispatchError, dispatch_worker


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "pat")
    calls: list[str] = []

    async def fake_dispatch(settings, trigger):
        calls.append(trigger)

    monkeypatch.setattr(main, "dispatch_worker", fake_dispatch)
    monkeypatch.setattr(main, "_auto_run_blocker", lambda: None)
    return calls


def test_cron_not_configured(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert TestClient(main.app).get("/api/cron").status_code == 503


@pytest.mark.parametrize("header", [None, "Bearer wrong", "s3cret"])
def test_cron_rejects_bad_secret(configured, header):
    headers = {"Authorization": header} if header else {}
    response = TestClient(main.app).get("/api/cron", headers=headers)
    assert response.status_code == 401 and configured == []


@pytest.mark.parametrize("method", ["get", "post"])
def test_cron_dispatches(configured, method):
    response = getattr(TestClient(main.app), method)("/api/cron", headers={"Authorization": "Bearer s3cret"})
    assert response.json() == {"dispatched": True} and configured == ["cron"]


def test_cron_skips_when_recent_run(configured, monkeypatch):
    monkeypatch.setattr(main, "_auto_run_blocker", lambda: "automatic run #7 already started at 12:05 UTC")
    response = TestClient(main.app).get("/api/cron", headers={"Authorization": "Bearer s3cret"})
    assert response.json()["dispatched"] is False and configured == []


async def test_dispatch_worker_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    settings = Settings(_env_file=None, github_dispatch_token="pat")
    await dispatch_worker(settings, "cron", transport=httpx.MockTransport(handler))
    assert seen == {
        "url": "https://api.github.com/repos/NellasTerton/skytec-metacritic/actions/workflows/worker.yml/dispatches",
        "auth": "Bearer pat",
        "body": {"ref": "main", "inputs": {"trigger": "cron"}},
    }


async def test_dispatch_worker_error():
    settings = Settings(_env_file=None, github_dispatch_token="pat")
    transport = httpx.MockTransport(lambda r: httpx.Response(403, text="Resource not accessible"))
    with pytest.raises(DispatchError, match="HTTP 403"):
        await dispatch_worker(settings, "cron", transport=transport)
    with pytest.raises(DispatchError, match="not set"):
        await dispatch_worker(Settings(_env_file=None), "cron")
