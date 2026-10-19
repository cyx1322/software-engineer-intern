"""Case statuses and the review state machine.

Every status change goes through next_status(), and TRANSITIONS is the only
place that says which changes are allowed. To change the workflow (for example,
to send a case back to stage 1), edit the table.
"""
from enum import Enum


class Status(str, Enum):
    PENDING = "pending"
    OCR_DONE = "ocr_done"
    OCR_FAILED = "ocr_failed"
    STAGE1_APPROVED = "stage1_approved"
    STAGE1_REJECTED = "stage1_rejected"
    STAGE2_APPROVED = "stage2_approved"
    STAGE2_REJECTED = "stage2_rejected"
    EXPORTED = "exported"


# (current status, event) -> next status. A status with no outgoing entry is
# terminal, which is how rejected cases are kept out of later stages and exports.
TRANSITIONS: dict[tuple[Status, str], Status] = {
    (Status.PENDING, "ocr_succeeded"): Status.OCR_DONE,
    (Status.PENDING, "ocr_failed"): Status.OCR_FAILED,
    # Processing a batch again re-queues transient failures only; ocr.start() skips unreadable scans.
    (Status.OCR_FAILED, "ocr_retry"): Status.PENDING,
    (Status.OCR_DONE, "stage1_approve"): Status.STAGE1_APPROVED,
    (Status.OCR_DONE, "stage1_reject"): Status.STAGE1_REJECTED,
    (Status.STAGE1_APPROVED, "stage2_approve"): Status.STAGE2_APPROVED,
    (Status.STAGE1_APPROVED, "stage2_reject"): Status.STAGE2_REJECTED,
    (Status.STAGE2_APPROVED, "export"): Status.EXPORTED,
}


class InvalidTransition(Exception):
    """The event is not allowed from the case's current status (HTTP 409)."""


def review_event(stage: int, decision: str) -> str:
    """Name the event for a review request, e.g. stage 1 + approve -> stage1_approve."""
    return f"stage{stage}_{decision}"


def next_status(current: Status, event: str) -> Status:
    """Return the status `event` leads to, or raise InvalidTransition with a reason."""
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        required = " or ".join(f"'{src.value}'" for src, ev in TRANSITIONS if ev == event)
        raise InvalidTransition(
            f"'{event}' is not allowed when case status is '{current.value}'; "
            f"it requires {required}"
        ) from None
