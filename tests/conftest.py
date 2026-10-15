import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "metacritic"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def fixture_for_path(path: str) -> str | None:
    """Путь backend.metacritic.com -> имя фикстуры (None — 404)."""
    parts = path.strip("/").split("/")
    if path == "/finder/metacritic/web":
        return None  # выбирается по query в transport
    if parts[:4] == ["composer", "metacritic", "pages", "games"]:
        return f"composer_{parts[4]}"
    if parts[0] == "reviews" and parts[-2] == "stats":
        kind, slug, platform = parts[2], parts[4], parts[6]
        return f"{kind}_stats_{slug}_{platform}"
    if parts[0] == "reviews":
        kind, slug, platform = parts[2], parts[4], parts[6]
        return f"reviews_{kind}_{slug}_{platform}"
    return None


def fixture_transport(overrides: dict[str, Callable[[httpx.Request], httpx.Response]] | None = None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if overrides and path in overrides:
            return overrides[path](request)
        if path == "/finder/metacritic/web":
            name = "finder_new_releases" if "metaScoreMin" in request.url.params else "finder_browse_newest"
        else:
            name = fixture_for_path(path)
        if name is None or not (FIXTURES / f"{name}.json").exists():
            if path.startswith("/composer/"):
                return httpx.Response(200, json=load("composer_not_found"))
            return httpx.Response(404, json={"errors": [{"code": 404}]})
        return httpx.Response(200, json=load(name))

    transport = httpx.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport


@pytest.fixture
def transport():
    return fixture_transport()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Тесты не читают локальный .env, а кэши настроек и движка сбрасываются между тестами."""
    from app import config, db

    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    for cached in (config.get_settings, db.get_engine, db._session_factory):
        cached.cache_clear()
    yield
    for cached in (config.get_settings, db.get_engine, db._session_factory):
        cached.cache_clear()


requires_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not set")
