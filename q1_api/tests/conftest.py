"""Shared fixtures: a throwaway database and an API client that uses it.

Tests use an in-memory MongoDB (mongomock) so `python -m pytest q1_api/` passes
from a clean checkout. Set TEST_MONGO_URL (e.g. mongodb://localhost:27017) to
run the same tests against a real server; each test gets its own database,
which is dropped afterwards.
"""
import os
import uuid
from pathlib import Path

import mongomock
import pymongo
import pytest
from fastapi.testclient import TestClient

from app.db import get_db, init_db
from app.main import app

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "data" / "account_applications.csv"


@pytest.fixture
def db():
    url = os.getenv("TEST_MONGO_URL")
    client = pymongo.MongoClient(url, tz_aware=True) if url else mongomock.MongoClient(tz_aware=True)
    name = f"idv_test_{uuid.uuid4().hex[:8]}"
    database = client[name]
    init_db(database)
    yield database
    client.drop_database(name)
    client.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def sample_csv() -> bytes:
    return SAMPLE_CSV.read_bytes()


@pytest.fixture
def upload(client):
    """Return a function that POSTs CSV bytes to the import endpoint."""
    def _upload(content: bytes, filename: str = "applications.csv"):
        return client.post("/api/batches", files={"file": (filename, content, "text/csv")})
    return _upload
