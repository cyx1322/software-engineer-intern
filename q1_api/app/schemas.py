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


class BatchOut(BaseModel):
    id: str
    filename: str | None
    created_at: datetime
    total_rows: int
    accepted_count: int
    rejected_count: int
    rejected_rows: list[RejectedRow]
    status_counts: dict[str, int]


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
    created_at: datetime
    updated_at: datetime


class CasePage(BaseModel):
    items: list[CaseOut]
    total: int
    page: int
    page_size: int
