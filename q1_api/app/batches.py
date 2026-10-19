"""Batch endpoints: import, summary, case listing, OCR processing, and export."""
import hashlib
import logging
from pathlib import Path
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, UploadFile
from pymongo import ASCENDING
from pymongo.database import Database

from app import ocr
from app.db import DB, get_or_404, to_api, utcnow
from app.export import NothingToExport, create_export, deliver
from app.schemas import BatchOut, CasePage, ExportOut, ProcessOut
from app.state_machine import Status
from app.validation import CsvFormatError, validate_csv

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.post("", response_model=BatchOut, status_code=201)
def create_batch(file: UploadFile, db: DB):
    """Import an applications CSV uploaded as the multipart form field `file`.

    Valid rows become `pending` cases. Invalid rows are not imported; they are
    listed in `rejected_rows` with the row number and every problem found.
    """
    try:
        batch = import_batch(db, file.file.read(), file.filename)
    except CsvFormatError as exc:
        raise HTTPException(422, f"{file.filename}: {exc}") from None
    return _batch_out(db, batch)


def import_batch(db: Database, content: bytes, filename: str | None, **fields) -> dict:
    """Validate a CSV and store its batch and cases; `fields` are extra batch fields."""
    accepted, rejected = validate_csv(content)
    now = utcnow()
    batch = {
        "_id": ObjectId(),
        "filename": filename,
        "created_at": now,
        "total_rows": len(accepted) + len(rejected),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejected_rows": rejected,
        **fields,
    }
    if accepted:
        db.cases.insert_many([
            {**row, "batch_id": batch["_id"], "status": Status.PENDING.value, "created_at": now, "updated_at": now}
            for row in accepted
        ])
    # Written after its cases, so a batch is never visible with only part of its cases.
    db.batches.insert_one(batch)
    return batch


def seed_batch(db: Database, path: str) -> dict:
    """Import the seed CSV once. A later start finds the batch by the file's hash and skips it."""
    content = Path(path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    existing = db.batches.find_one({"seed_sha256": digest})
    if existing:
        log.info("seed %s already imported as batch %s", path, existing["_id"])
        return existing
    batch = import_batch(db, content, Path(path).name, seed_sha256=digest)
    log.info("seed %s imported as batch %s (%d cases)", path, batch["_id"], batch["accepted_count"])
    return batch


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: str, db: DB):
    return _batch_out(db, get_or_404(db.batches, batch_id, "batch"))


@router.get("/{batch_id}/cases", response_model=CasePage)
def list_cases(
    batch_id: str,
    db: DB,
    status: Status | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List a batch's cases in file order, optionally filtered by status."""
    batch = get_or_404(db.batches, batch_id, "batch")
    query = {"batch_id": batch["_id"]}
    if status is not None:
        query["status"] = status.value
    cursor = db.cases.find(query).sort("row", ASCENDING).skip((page - 1) * page_size).limit(page_size)
    return {
        "items": [to_api(case) for case in cursor],
        "total": db.cases.count_documents(query),
        "page": page,
        "page_size": page_size,
    }


@router.post("/{batch_id}/process", response_model=ProcessOut, status_code=202)
def process_batch(batch_id: str, db: DB):
    """Queue the batch's pending cases for OCR and return immediately.

    Poll GET /api/batches/{id} for progress. Calling this again after a restart
    picks up cases that were still pending and retries transient OCR failures.
    """
    batch = get_or_404(db.batches, batch_id, "batch")
    queued = ocr.start(db, batch["_id"])
    return {**_batch_out(db, batch), "queued": queued}


@router.post(
    "/{batch_id}/export",
    response_model=ExportOut,
    status_code=201,
    responses={409: {"description": "No stage2_approved cases are waiting for export"}},
)
def export_batch(batch_id: str, db: DB):
    """Export every stage2_approved case to a new CSV and deliver it to account opening.

    The response is returned after delivery finishes. If every retry fails,
    the export is still created with delivery.status `failed`, and
    POST /api/exports/{id}/deliver retries it with the same key.
    """
    batch = get_or_404(db.batches, batch_id, "batch")
    try:
        export = create_export(db, batch["_id"])
    except NothingToExport as exc:
        raise HTTPException(409, str(exc)) from None
    return to_api(deliver(db, export))


def _batch_out(db: Database, batch: dict) -> dict:
    """Batch document plus a count for every status (zeros included), OCR progress, and exports."""
    counts = {status.value: 0 for status in Status}
    pipeline = [{"$match": {"batch_id": batch["_id"]}}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    for group in db.cases.aggregate(pipeline):
        counts[group["_id"]] = group["count"]
    exports = db.exports.find({"batch_id": batch["_id"]}).sort("created_at", ASCENDING)
    return {
        **to_api(batch),
        "status_counts": counts,
        "processing": ocr.is_processing(batch["_id"]),
        "exports": [to_api(export) for export in exports],
    }
