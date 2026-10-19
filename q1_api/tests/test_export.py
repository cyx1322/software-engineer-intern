"""Q2.3: export of stage2_approved cases and delivery with one idempotency key per export."""
import csv
import hashlib
import io

import httpx
import pytest
from bson import ObjectId

from app import config
from app.db import transition_case
from app.export import create_export, deliver

CSV = b"case_ref,applicant_name,id_number,dob,issue_date,image_ref\n" + b"".join(
    f"APPL-{n},Test Applicant {n},TEST-ID-{n},1990-01-01,2020-01-01,id_scan_000{n}.jpg\n".encode() for n in range(1, 6)
)
FIELDS = {"id_number": "TEST-ID-1", "full_name": "Test", "dob": "1990-01-01", "issue_date": "2020-01-01", "confidence": 0.9}
APPROVED = ("ocr_succeeded", "stage1_approve", "stage2_approve")


class FakeOnboarding:
    """Behaves like mock_onboarding: a repeated Idempotency-Key gets the original
    receipt back without a new ingestion. `script` sets the outcome of each
    attempt with a new key: "500", "lost" (ingested, but no response), or "ok"."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.receipts = {}  # one entry per ingestion

    def __call__(self, request):
        self.requests.append(request)
        key = request.headers["Idempotency-Key"]
        if key in self.receipts:
            self.receipts[key]["attempts"] += 1
            return httpx.Response(200, json={**self.receipts[key], "deduplicated": True})
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "500":
            return httpx.Response(500, json={"error": "ingestion temporarily unavailable"})
        self.receipts[key] = {
            "delivery_id": f"DLV-{len(self.receipts) + 1:04d}",
            "idempotency_key": key,
            "file_name": request.headers["X-File-Name"],
            "sha256": hashlib.sha256(request.content).hexdigest(),
            "attempts": 1,
        }
        if outcome == "lost":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=self.receipts[key])

    def keys(self):
        return [request.headers["Idempotency-Key"] for request in self.requests]


@pytest.fixture
def batch_id(upload):
    return upload(CSV).json()["id"]


def move(db, batch_id, case_ref, *events):
    """Drive a case through state-machine events, as OCR and the reviewers would."""
    case = db.cases.find_one({"batch_id": ObjectId(batch_id), "case_ref": case_ref})
    for event in events:
        case = transition_case(db, case, event, {"ocr": FIELDS} if event == "ocr_succeeded" else None)
    return case


def export(client, batch_id):
    return client.post(f"/api/batches/{batch_id}/export")


def status_of(db, batch_id, case_ref):
    return db.cases.find_one({"batch_id": ObjectId(batch_id), "case_ref": case_ref})["status"]


def test_export_contains_exactly_the_stage2_approved_cases(client, db, fake_onboarding, batch_id):
    move(db, batch_id, "APPL-1", *APPROVED)
    move(db, batch_id, "APPL-2", "ocr_succeeded", "stage1_approve")
    move(db, batch_id, "APPL-3", "ocr_succeeded", "stage1_approve", "stage2_reject")
    move(db, batch_id, "APPL-4", *APPROVED)
    fake = FakeOnboarding()
    fake_onboarding(fake)

    resp = export(client, batch_id)

    assert resp.status_code == 201
    exp = resp.json()
    assert exp["case_refs"] == ["APPL-1", "APPL-4"]
    file = client.get(f"/api/exports/{exp['id']}/file")
    assert file.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(file.text)))
    assert [row["case_ref"] for row in rows] == ["APPL-1", "APPL-4"]
    assert rows[0]["ocr_full_name"] == "Test"

    assert [status_of(db, batch_id, f"APPL-{n}") for n in range(1, 6)] == [
        "exported", "stage1_approved", "stage2_rejected", "exported", "pending",
    ]

    # The file sent is the export file, under a key derived from the export's id.
    assert exp["filename"] == exp["idempotency_key"] == f"EXPORT-{exp['id']}.csv"
    (sent,) = fake.requests
    assert sent.headers["Idempotency-Key"] == exp["idempotency_key"]
    assert sent.headers["X-File-Name"] == exp["filename"]
    assert sent.content == file.content and exp["sha256"] == hashlib.sha256(file.content).hexdigest()

    assert exp["delivery"]["status"] == "delivered" and exp["delivery"]["attempts"] == 1
    assert exp["delivery"]["receipt"]["delivery_id"] == "DLV-0001"
    batch = client.get(f"/api/batches/{batch_id}").json()
    assert [(e["id"], e["delivery"]["status"]) for e in batch["exports"]] == [(exp["id"], "delivered")]


def test_later_export_only_includes_new_approvals(client, db, fake_onboarding, batch_id):
    fake = FakeOnboarding()
    fake_onboarding(fake)
    move(db, batch_id, "APPL-1", *APPROVED)
    first = export(client, batch_id).json()

    nothing_new = export(client, batch_id)
    assert nothing_new.status_code == 409
    assert nothing_new.json()["detail"] == "batch has no stage2_approved cases to export"

    move(db, batch_id, "APPL-2", *APPROVED)
    second = export(client, batch_id).json()
    assert second["case_refs"] == ["APPL-2"]
    assert second["idempotency_key"] != first["idempotency_key"]
    assert len(fake.receipts) == 2


@pytest.mark.parametrize(
    "script, attempts",
    [
        pytest.param(["lost"], 2, id="lost-response"),
        pytest.param(["500", "500"], 3, id="two-500s"),
    ],
)
def test_failed_attempts_are_retried_with_the_same_key(client, db, fake_onboarding, batch_id, script, attempts):
    fake = FakeOnboarding(*script)
    fake_onboarding(fake)
    move(db, batch_id, "APPL-1", *APPROVED)

    exp = export(client, batch_id).json()

    assert exp["delivery"]["status"] == "delivered" and exp["delivery"]["attempts"] == attempts
    assert fake.keys() == [exp["idempotency_key"]] * attempts
    assert len(fake.receipts) == 1  # ingested once


def test_exhausted_delivery_is_recorded_and_can_be_retried(client, db, fake_onboarding, batch_id):
    limit = config.ONBOARDING_MAX_ATTEMPTS
    fake = FakeOnboarding(*["500"] * limit)
    fake_onboarding(fake)
    move(db, batch_id, "APPL-1", *APPROVED)

    exp = export(client, batch_id).json()
    assert exp["delivery"] == {
        "status": "failed",
        "attempts": limit,
        "receipt": None,
        "error": f"no result after {limit} attempts; last error: HTTP 500: ingestion temporarily unavailable",
        "updated_at": exp["delivery"]["updated_at"],
    }
    assert status_of(db, batch_id, "APPL-1") == "exported"  # it stays in this export

    retried = client.post(f"/api/exports/{exp['id']}/deliver").json()
    assert retried["delivery"]["status"] == "delivered" and retried["delivery"]["attempts"] == limit + 1
    assert client.post(f"/api/exports/{exp['id']}/deliver").json()["delivery"] == retried["delivery"]
    assert fake.keys() == [exp["idempotency_key"]] * (limit + 1)  # nothing sent once delivered
    assert len(fake.receipts) == 1


def always_500(request):
    return httpx.Response(500, json={"error": "ingestion temporarily unavailable"})


@pytest.mark.parametrize("first_succeeds", [True, False], ids=["success-then-failure", "failure-then-success"])
def test_overlapping_deliveries_keep_the_success_and_count_every_attempt(db, fake_onboarding, batch_id, first_succeeds):
    """Two deliver() calls that both read the export while it was pending, finishing in either order."""
    move(db, batch_id, "APPL-1", *APPROVED)
    pending = create_export(db, ObjectId(batch_id))
    limit = config.ONBOARDING_MAX_ATTEMPTS

    for handler in [FakeOnboarding(), always_500] if first_succeeds else [always_500, FakeOnboarding()]:
        fake_onboarding(handler)
        final = deliver(db, pending)

    assert final["delivery"]["status"] == "delivered" and final["delivery"]["error"] is None
    assert final["delivery"]["receipt"]["delivery_id"] == "DLV-0001"
    assert final["delivery"]["attempts"] == 1 + limit


def test_cases_claimed_before_a_crash_are_exported_under_the_same_id(client, db, fake_onboarding, batch_id):
    fake_onboarding(FakeOnboarding())
    case = move(db, batch_id, "APPL-1", *APPROVED)
    claimed_by = ObjectId()
    transition_case(db, case, "export", {"export_id": claimed_by})  # the process stopped here

    exp = export(client, batch_id).json()

    assert exp["id"] == str(claimed_by)
    assert exp["idempotency_key"] == f"EXPORT-{claimed_by}.csv"
    assert exp["case_refs"] == ["APPL-1"]


def test_unknown_export_is_404(client):
    assert client.post(f"/api/exports/{ObjectId()}/deliver").status_code == 404
    assert client.get("/api/exports/not-an-id/file").status_code == 404
