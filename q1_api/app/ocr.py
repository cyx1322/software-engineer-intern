"""Background OCR processing.

POST /api/batches/{id}/process queues the batch's pending cases for a pool of
worker threads in this process and returns at once. A worker calls the OCR
service with retries, then moves the case to ocr_done or ocr_failed in one
atomic update that stores the result together with the new status.

There is no "processing" status. A case stays pending until its result is
written, so if the process stops at any point, unfinished cases are still
pending and processing the batch again picks them up. OCR results depend only
on the image, so a repeated attempt stores the same fields.
"""
import logging
import queue
import threading

import httpx
from bson import ObjectId
from pydantic import ValidationError
from pymongo import ASCENDING
from pymongo.database import Database

from app import config
from app.db import transition_case
from app.retry import PermanentError, RetriesExhausted, post_with_retries
from app.schemas import OcrFields
from app.state_machine import InvalidTransition, Status

log = logging.getLogger(__name__)
http = httpx.Client(timeout=httpx.Timeout(config.OCR_TIMEOUT_SECONDS, connect=2.0))

_jobs: queue.Queue = queue.Queue()
_lock = threading.Lock()
_queued: dict[ObjectId, int] = {}  # batch id -> cases queued or in progress in this process
_workers: list[threading.Thread] = []


def is_processing(batch_id: ObjectId) -> bool:
    return batch_id in _queued


def start(db: Database, batch_id: ObjectId) -> int:
    """Queue the batch's pending cases and return how many were queued.

    Cases whose retries ran out go back to pending first. Unreadable scans stay
    ocr_failed, because the same image cannot give a different answer. If the
    batch is already being processed, nothing new is queued.
    """
    with _lock:
        if is_processing(batch_id):
            return 0
        retryable = {"batch_id": batch_id, "status": Status.OCR_FAILED.value, "ocr_error.retryable": True}
        for case in db.cases.find(retryable):
            transition_case(db, case, "ocr_retry")
        pending = db.cases.find({"batch_id": batch_id, "status": Status.PENDING.value}, {"_id": 1})
        case_ids = [case["_id"] for case in pending.sort("row", ASCENDING)]
        if case_ids:
            _queued[batch_id] = len(case_ids)
            _start_workers()
    for case_id in case_ids:
        _jobs.put((db, batch_id, case_id))
    log.info("batch %s: queued %d cases for OCR", batch_id, len(case_ids))
    return len(case_ids)


def _start_workers() -> None:
    # Daemon threads end with the process. That is safe because nothing is
    # written for a case until its OCR result is known.
    while len(_workers) < config.OCR_WORKERS:
        worker = threading.Thread(target=_work, name=f"ocr-worker-{len(_workers) + 1}", daemon=True)
        worker.start()
        _workers.append(worker)


def _work() -> None:
    while True:
        db, batch_id, case_id = _jobs.get()
        try:
            process_case(db, case_id)
        except Exception:  # for example, the database is unreachable
            log.exception("case %s: OCR processing stopped; the case stays pending", case_id)
        finally:
            with _lock:
                _queued[batch_id] -= 1
                if _queued[batch_id] == 0:
                    del _queued[batch_id]
                    log.info("batch %s: OCR processing finished", batch_id)


def process_case(db: Database, case_id: ObjectId) -> None:
    case = db.cases.find_one({"_id": case_id, "status": Status.PENDING.value})
    if case is None:
        return  # another run already finished it
    event, fields = run_ocr(case)
    try:
        transition_case(db, case, event, fields)
    except InvalidTransition:
        log.info("case %s: finished by another worker first", case["case_ref"])
        return
    if event == "ocr_failed":
        log.warning("case %s: ocr_failed: %s", case["case_ref"], fields["ocr_error"]["reason"])


def run_ocr(case: dict) -> tuple[str, dict]:
    """Call OCR for one case; return the state-machine event and the fields to store."""
    try:
        response, attempts = post_with_retries(
            http,
            config.OCR_URL,
            max_attempts=config.OCR_MAX_ATTEMPTS,
            label=f"OCR {case['case_ref']}",
            json={"case_id": case["case_ref"], "image_ref": case["image_ref"]},
        )
        result = OcrFields.model_validate_json(response.content)
    except PermanentError as exc:  # 422: unreadable scan; record the service's reason as given
        return "ocr_failed", _error(exc.reason, retryable=False, attempts=exc.attempts)
    except RetriesExhausted as exc:  # every attempt hit a 500, a timeout, or a connection error
        return "ocr_failed", _error(str(exc), retryable=True, attempts=exc.attempts)
    except ValidationError as exc:
        return "ocr_failed", _error(f"invalid OCR response ({exc.error_count()} field errors)", False, attempts)
    return "ocr_succeeded", {"ocr": result.model_dump(), "ocr_error": None}


def _error(reason: str, retryable: bool, attempts: int) -> dict:
    return {"ocr_error": {"reason": reason, "retryable": retryable, "attempts": attempts}}
