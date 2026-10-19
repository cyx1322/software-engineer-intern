"""Export stage2_approved cases to CSV and deliver each export to account opening.

Creating an export starts by claiming its cases. Each stage2_approved case
moves to exported and is tagged with the new export's id, in one
compare-and-set update per case. A case therefore belongs to exactly one
export, and later exports skip it.

The export id is fixed at that point, and both the filename and the
Idempotency-Key are derived from it. Every delivery attempt for an export,
including a retry after a lost response or after a restart, sends the same
key, so the account-opening service ingests the export once. A new export has
a new id, so its key is always different.
"""
import csv
import hashlib
import io
import threading

import httpx
from bson import ObjectId
from pymongo import ASCENDING
from pymongo.database import Database

from app import config
from app.db import transition_case, utcnow
from app.retry import PermanentError, RetriesExhausted, post_with_retries
from app.state_machine import InvalidTransition, Status

http = httpx.Client(timeout=httpx.Timeout(config.ONBOARDING_TIMEOUT_SECONDS, connect=2.0))
_lock = threading.Lock()  # build one export at a time in this process

CASE_COLUMNS = ("case_ref", "applicant_name", "id_number", "dob", "issue_date", "image_ref")
OCR_COLUMNS = ("id_number", "full_name", "dob", "issue_date", "confidence")


class NothingToExport(Exception):
    pass


def create_export(db: Database, batch_id: ObjectId) -> dict:
    """Claim the batch's stage2_approved cases and store their CSV as a new export."""
    with _lock:
        export_id = _unrecorded_export_id(db, batch_id) or ObjectId()
        for case in db.cases.find({"batch_id": batch_id, "status": Status.STAGE2_APPROVED.value}):
            try:
                transition_case(db, case, "export", {"export_id": export_id})
            except InvalidTransition:
                pass  # its status changed after the query, so it is not stage2_approved now
        cases = list(db.cases.find({"export_id": export_id}).sort("row", ASCENDING))
        if not cases:
            raise NothingToExport("batch has no stage2_approved cases to export")

        content = render_csv(cases)
        filename = f"EXPORT-{export_id}.csv"
        export = {
            "_id": export_id,
            "batch_id": batch_id,
            "filename": filename,
            "idempotency_key": filename,
            "created_at": utcnow(),
            "case_refs": [case["case_ref"] for case in cases],
            "csv": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "delivery": {"status": "pending", "attempts": 0, "receipt": None, "error": None},
        }
        db.exports.insert_one(export)
        return export


def _unrecorded_export_id(db: Database, batch_id: ObjectId) -> ObjectId | None:
    """Find cases claimed by an export that has no record yet (the process stopped in between).

    Reusing that id gives the recovered export the same filename and key.
    """
    claimed = set(db.cases.distinct("export_id", {"batch_id": batch_id, "status": Status.EXPORTED.value}))
    recorded = set(db.exports.distinct("_id", {"batch_id": batch_id}))
    return next(iter(claimed - recorded), None)


def render_csv(cases: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([*CASE_COLUMNS, *(f"ocr_{column}" for column in OCR_COLUMNS)])
    for case in cases:
        ocr = case.get("ocr") or {}
        writer.writerow([*(case[column] for column in CASE_COLUMNS), *(ocr.get(column, "") for column in OCR_COLUMNS)])
    return out.getvalue()


def deliver(db: Database, export: dict) -> dict:
    """Send the export with its own Idempotency-Key, retrying on 500s and timeouts; store the receipt."""
    if export["delivery"]["status"] == "delivered":
        return export
    try:
        response, attempts = post_with_retries(
            http,
            config.ONBOARDING_URL,
            max_attempts=config.ONBOARDING_MAX_ATTEMPTS,
            label=f"delivery {export['filename']}",
            content=export["csv"].encode(),
            headers={
                "Idempotency-Key": export["idempotency_key"],
                "X-File-Name": export["filename"],
                "Content-Type": "text/csv",
            },
        )
        result = {"status": "delivered", "receipt": response.json(), "error": None}
    except (PermanentError, RetriesExhausted) as exc:
        attempts = exc.attempts
        result = {"status": "failed", "receipt": None, "error": str(exc)}

    # Two calls can overlap, for example a redeliver while the first delivery is
    # still retrying. Each call adds its own attempts, and nothing replaces a
    # delivered result, whichever call finishes last.
    db.exports.update_one(
        {"_id": export["_id"]},
        {"$inc": {"delivery.attempts": attempts}, "$set": {"delivery.updated_at": utcnow()}},
    )
    db.exports.update_one(
        {"_id": export["_id"], "delivery.status": {"$ne": "delivered"}},
        {"$set": {f"delivery.{field}": value for field, value in result.items()}},
    )
    return db.exports.find_one({"_id": export["_id"]})
