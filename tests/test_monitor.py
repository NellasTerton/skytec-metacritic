import httpx
import pytest
from fastapi.testclient import TestClient

from app import github, main
from app.config import Settings

STATUS = {
    "now": "2026-10-15T13:00:00+00:00",
    "totals": {"games": 1, "games_scored": 1, "summaries": 0, "runs_today": 1, "processed_today": 1},
    "runs": [],
}


@pytest.fixture
def monitor(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "pat")
    state = {"github": None, "active": None, "dispatched": []}

    async def fake_latest(settings, use_cache=True):
        return state["github"]

    async def fake_dispatch(settings, trigger):
        state["dispatched"].append(trigger)

    monkeypatch.setattr(main, "latest_worker_run", fake_latest)
    monkeypatch.setattr(main, "dispatch_worker", fake_dispatch)
    monkeypatch.setattr(main, "_monitor_data", lambda: {**STATUS, "active_run_id": state["active"]})
    monkeypatch.setattr(main, "_active_run_id", lambda: state["active"])
    return state


def test_status_idle_allows_run(monitor):
    body = TestClient(main.app).get("/api/status").json()
    assert body["can_run"] is True and body["run_blocker"] is None and body["totals"]["games"] == 1
    assert "active_run_id" not in body


def test_status_blocks_when_github_busy(monitor):
    monitor["github"] = {"status": "queued", "active": True}
    body = TestClient(main.app).get("/api/status").json()
    assert body["can_run"] is False and "очереди" in body["run_blocker"]


def test_run_dispatches_web_trigger(monitor):
    response = TestClient(main.app).post("/api/run")
    assert response.json() == {"dispatched": True} and monitor["dispatched"] == ["web"]


def test_run_refuses_while_running(monitor):
    monitor["active"] = 42
    response = TestClient(main.app).post("/api/run")
    assert response.status_code == 409 and "#42" in response.json()["reason"] and monitor["dispatched"] == []


def test_run_not_configured(monitor, monkeypatch):
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN")
    assert TestClient(main.app).post("/api/run").status_code == 503


def test_monitor_page_renders():
    response = TestClient(main.app).get("/monitor")
    assert response.status_code == 200 and "Запустить сейчас" in response.text


async def test_latest_worker_run_parses_and_caches():
    github._status_cache.clear()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path.endswith("/actions/workflows/worker.yml/runs")
        run = {
            "status": "in_progress",
            "conclusion": None,
            "event": "workflow_dispatch",
            "created_at": "2026-10-15T13:00:00Z",
            "html_url": "https://github.com/x/runs/1",
        }
        return httpx.Response(200, json={"workflow_runs": [run]})

    settings = Settings(_env_file=None, github_dispatch_token="pat")
    transport = httpx.MockTransport(handler)
    first = await github.latest_worker_run(settings, transport=transport)
    second = await github.latest_worker_run(settings, transport=transport)
    assert first == second and first["active"] is True and first["url"].endswith("/runs/1")
    assert calls["n"] == 1  # второй ответ из кэша
    github._status_cache.clear()


async def test_latest_worker_run_without_token():
    assert await github.latest_worker_run(Settings(_env_file=None)) is None


def test_run_games_endpoint(monkeypatch):
    from contextlib import contextmanager

    from app import queries

    @contextmanager
    def scope(*args, **kwargs):
        yield None

    monkeypatch.setattr(main, "session_scope", scope)
    monkeypatch.setattr(queries, "run_games", lambda s, run_id: {"id": run_id, "games": []} if run_id == 7 else None)
    client = TestClient(main.app)
    assert client.get("/api/runs/7/games").json() == {"id": 7, "games": []}
    assert client.get("/api/runs/8/games").status_code == 404


def test_monitor_page_has_first_run_card():
    html = TestClient(main.app).get("/monitor").text
    assert "Первый прогон сегодня" in html and "metacritic.com/game/" in html and "run-games-btn" in html
