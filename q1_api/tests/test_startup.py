"""Q3.2 liveness and readiness, and the idempotent seed import used by Compose."""
from pathlib import Path

from pymongo.errors import ServerSelectionTimeoutError

from app import main
from app.batches import seed_batch

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "data" / "account_applications.csv"


def test_ready_when_the_database_answers(client, db, monkeypatch):
    monkeypatch.setattr(main, "get_db", lambda: db)
    resp = client.get("/api/ready")
    assert resp.status_code == 200 and resp.json() == {"status": "ready"}


def test_not_ready_but_alive_when_the_database_is_unreachable(client, monkeypatch):
    def unreachable():
        raise ServerSelectionTimeoutError("mongo:27017: [Errno -2] Name or service not known")

    monkeypatch.setattr(main, "get_db", unreachable)

    ready = client.get("/api/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not ready", "reason": "database unreachable"}
    assert client.get("/api/health").status_code == 200


def test_seeding_twice_imports_the_file_once(db):
    first = seed_batch(db, str(SAMPLE_CSV))
    again = seed_batch(db, str(SAMPLE_CSV))

    assert again["_id"] == first["_id"]
    assert db.batches.count_documents({}) == 1
    assert db.cases.count_documents({}) == 43
