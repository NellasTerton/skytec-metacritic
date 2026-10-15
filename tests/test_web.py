from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import requires_db


def test_health_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(app).get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "not_configured"}


def test_index_without_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "временно недоступен" in response.text


@requires_db
def test_health_with_db():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "migration": "0002"}
