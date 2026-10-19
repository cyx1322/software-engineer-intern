"""Batch import, batch summary, and case listing endpoints."""
from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, UploadFile
from pymongo import ASCENDING
from pymongo.database import Database

from app.db import DB, get_or_404, to_api, utcnow
from app.schemas import BatchOut, CasePage
from app.state_machine import Status
from app.validation import CsvFormatError, validate_csv

router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.post("", response_model=BatchOut, status_code=201)
def create_batch(file: UploadFile, db: DB):
    """Import an applications CSV uploaded as the multipart form field `file`.

    Valid rows become `pending` cases. Invalid rows are not imported; they are
    listed in `rejected_rows` with the row number and every problem found.
    """
    try:
        accepted, rejected = validate_csv(file.file.read())
    except CsvFormatError as exc:
        raise HTTPException(422, f"{file.filename}: {exc}") from None

    now = utcnow()
    batch = {
        "_id": ObjectId(),
        "filename": file.filename,
        "created_at": now,
        "total_rows": len(accepted) + len(rejected),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "rejected_rows": rejected,
    }
    if accepted:
        db.cases.insert_many([
            {**row, "batch_id": batch["_id"], "status": Status.PENDING.value, "created_at": now, "updated_at": now}
            for row in accepted
        ])
    # Written after its cases, so a batch is never visible with only part of its cases.
    db.batches.insert_one(batch)
    return _batch_out(db, batch)


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


def _batch_out(db: Database, batch: dict) -> dict:
    """Batch document plus a count for every status, including zeros."""
    counts = {status.value: 0 for status in Status}
    pipeline = [{"$match": {"batch_id": batch["_id"]}}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    for group in db.cases.aggregate(pipeline):
        counts[group["_id"]] = group["count"]
    return {**to_api(batch), "status_counts": counts}
