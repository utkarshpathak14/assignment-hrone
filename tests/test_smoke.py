from fastapi.testclient import TestClient
from pymongo import MongoClient

import app.main as main


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_mongo_is_down(client, monkeypatch):
    # Nothing listens on port 1, so the ping fails after the short server-selection timeout.
    dead = MongoClient("mongodb://localhost:1", serverSelectionTimeoutMS=200)
    monkeypatch.setattr(main, "client", dead)

    response = client.get("/health")

    assert response.status_code == 503
    assert "detail" in response.json()


def test_startup_is_idempotent():
    # Starting the app twice runs ensure_indexes() twice; the second run must not fail.
    with TestClient(main.app):
        pass
    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200
