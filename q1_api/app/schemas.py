"""Request and response models for the HTTP API."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.state_machine import Status


class ReviewRequest(BaseModel):
    stage: Literal[1, 2]
    decision: Literal["approve", "reject"]


class RejectedRow(BaseModel):
    row: int
    case_ref: str | None
    reasons: list[str]


class OcrFields(BaseModel):
    """Fields from a successful OCR response, stored as returned."""
    id_number: str
    full_name: str
    dob: str
    issue_date: str
    confidence: float


class OcrError(BaseModel):
    reason: str  # for a 4xx, the OCR service's own message
    retryable: bool  # True when retries ran out; processing the batch again re-queues the case
    attempts: int


class Delivery(BaseModel):
    status: Literal["pending", "delivered", "failed"]
    attempts: int
    receipt: dict | None  # the account-opening service's receipt, as returned
    error: str | None
    updated_at: datetime | None = None


class ExportOut(BaseModel):
    id: str
    batch_id: str
    filename: str
    idempotency_key: str
    created_at: datetime
    case_refs: list[str]
    sha256: str
    delivery: Delivery


class BatchOut(BaseModel):
    id: str
    filename: str | None
    created_at: datetime
    total_rows: int
    accepted_count: int
    rejected_count: int
    rejected_rows: list[RejectedRow]
    status_counts: dict[str, int]
    processing: bool  # OCR work for this batch is queued or running
    exports: list[ExportOut]


class ProcessOut(BatchOut):
    queued: int


class CaseOut(BaseModel):
    id: str
    batch_id: str
    row: int
    case_ref: str
    applicant_name: str
    id_number: str
    dob: str
    issue_date: str
    image_ref: str
    status: Status
    ocr: OcrFields | None = None
    ocr_error: OcrError | None = None
    export_id: str | None = None
    created_at: datetime
    updated_at: datetime


class CasePage(BaseModel):
    items: list[CaseOut]
    total: int
    page: int
    page_size: int
