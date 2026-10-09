"""Shared pytest fixtures.

The test suite runs against a separate database (attendance_test) so it never touches your dev data.
The env var must be set BEFORE app.main is imported, because main.py reads it at import time.
"""
import os

TEST_DB = "attendance_test"
os.environ["MONGO_DB"] = TEST_DB

import pathlib
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, db

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE_PORT = 8001


@pytest.fixture
def clean_db():
    """Empty every collection (indexes are kept) so each test starts from a known state."""
    for name in db.list_collection_names():
        db[name].delete_many({})
    return db


@pytest.fixture
def client(clean_db):
    """An in-process test client. `with TestClient(app)` runs the app's lifespan, like uvicorn would."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def live_url():
    """A real uvicorn server on port 8001, for tests that need truly parallel requests.

    Started once per test session (scope="session") and stopped when the session ends.
    """
    env = {**os.environ, "MONGO_DB": TEST_DB}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(LIVE_PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{LIVE_PORT}"
    deadline = time.time() + 20
    while True:
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.TransportError:
            pass
        if time.time() > deadline:
            proc.kill()
            raise RuntimeError("live server did not become healthy within 20 s")
        time.sleep(0.2)
    yield url
    proc.terminate()
    proc.wait()
