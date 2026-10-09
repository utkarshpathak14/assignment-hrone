"""Shared pytest fixtures.

The test suite runs against a separate database (attendance_test) so it never touches your dev data.
The env var must be set BEFORE app.main is imported, because main.py reads it at import time.
"""
import os

os.environ["MONGO_DB"] = "attendance_test"

import pytest
from fastapi.testclient import TestClient

from app.main import app, db


@pytest.fixture
def client():
    """A test client with a clean database for every test.

    `with TestClient(app)` runs the app's startup/shutdown (lifespan) code, just like uvicorn would.
    """
    for name in db.list_collection_names():
        db[name].delete_many({})
    with TestClient(app) as c:
        yield c
