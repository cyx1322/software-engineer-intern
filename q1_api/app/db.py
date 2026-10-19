"""MongoDB connection, indexes, and shared data-access helpers.

Collections and fields are documented in q1_api/README.md ("MongoDB schema").
"""
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated

from bson import ObjectId
from fastapi import Depends, HTTPException
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

from app import config
from app.state_machine import InvalidTransition, Status, next_status


@lru_cache
def get_db() -> Database:
    """Connect on first use."""
    client = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=5000, tz_aware=True)
    db = client[config.MONGO_DB]
    init_db(db)
    return db


DB = Annotated[Database, Depends(get_db)]


def init_db(db: Database) -> None:
    """Create indexes; create_index does nothing if the index already exists."""
    # A case_ref identifies one application within a batch.
    db.cases.create_index([("batch_id", ASCENDING), ("case_ref", ASCENDING)], unique=True)
    # Case listing in file order, with and without a status filter; also status counts.
    db.cases.create_index([("batch_id", ASCENDING), ("row", ASCENDING)])
    db.cases.create_index([("batch_id", ASCENDING), ("status", ASCENDING), ("row", ASCENDING)])
    # Building an export reads back the cases it claimed.
    db.cases.create_index([("export_id", ASCENDING)], sparse=True)
    db.exports.create_index([("batch_id", ASCENDING), ("created_at", ASCENDING)])
    # At most one batch per seed file, even if two API processes start at once.
    db.batches.create_index([("seed_sha256", ASCENDING)], unique=True, sparse=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_404(collection: Collection, doc_id: str, label: str) -> dict:
    """Fetch a document by its string id; a malformed id is also a 404."""
    doc = collection.find_one({"_id": ObjectId(doc_id)}) if ObjectId.is_valid(doc_id) else None
    if doc is None:
        raise HTTPException(404, f"{label} {doc_id} not found")
    return doc


def to_api(doc: dict) -> dict:
    """Rename _id to id and turn ObjectIds into strings for JSON output."""
    fields = {k: str(v) if isinstance(v, ObjectId) else v for k, v in doc.items() if k != "_id"}
    return {"id": str(doc["_id"]), **fields}


def transition_case(db: Database, case: dict, event: str, fields: dict | None = None) -> dict:
    """Apply a state-machine event to a case and return the updated document.

    `fields` (for example OCR results) are written in the same update as the
    new status. The update only matches if the case still has the status that
    was checked, so two concurrent requests cannot both move the same case:
    the second one finds nothing to update and gets InvalidTransition (HTTP 409).
    """
    current = Status(case["status"])
    new = next_status(current, event)
    updated = db.cases.find_one_and_update(
        {"_id": case["_id"], "status": current.value},
        {"$set": {**(fields or {}), "status": new.value, "updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise InvalidTransition(f"case status changed from '{current.value}' by another request; reload and retry")
    return updated
