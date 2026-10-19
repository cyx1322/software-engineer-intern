"""Q1.2 review state machine: the approval path and rejected transitions."""
import pytest
from bson import ObjectId

from app.db import transition_case
from app.state_machine import InvalidTransition

ONE_CASE_CSV = (
    b"case_ref,applicant_name,id_number,dob,issue_date,image_ref\n"
    b"APPL-T-0001,Test Applicant,TEST-ID-900001,1990-01-01,2020-01-01,id_scan_9001.jpg\n"
)


@pytest.fixture
def batch_id(upload):
    return upload(ONE_CASE_CSV).json()["id"]


@pytest.fixture
def case_id(client, batch_id):
    return client.get(f"/api/batches/{batch_id}/cases").json()["items"][0]["id"]


def finish_ocr(db, case_id, event="ocr_succeeded"):
    """Stand-in for the Module 2 OCR worker, which applies the same events."""
    transition_case(db, db.cases.find_one({"_id": ObjectId(case_id)}), event)


def review(client, case_id, stage, decision):
    return client.post(f"/api/cases/{case_id}/review", json={"stage": stage, "decision": decision})


def status_of(db, case_id):
    return db.cases.find_one({"_id": ObjectId(case_id)})["status"]


def test_successful_lifecycle(client, db, batch_id, case_id):
    assert status_of(db, case_id) == "pending"
    finish_ocr(db, case_id)

    stage1 = review(client, case_id, 1, "approve")
    assert stage1.status_code == 200
    assert stage1.json()["status"] == "stage1_approved"

    stage2 = review(client, case_id, 2, "approve")
    assert stage2.status_code == 200
    assert stage2.json()["status"] == "stage2_approved"

    counts = client.get(f"/api/batches/{batch_id}").json()["status_counts"]
    assert counts["stage2_approved"] == 1
    assert counts["pending"] == counts["ocr_done"] == counts["stage1_approved"] == 0


def test_stage2_review_before_stage1_finished_is_409(client, db, case_id):
    finish_ocr(db, case_id)

    resp = review(client, case_id, 2, "approve")

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "'stage2_approve' is not allowed when case status is 'ocr_done'; it requires 'stage1_approved'"
    )
    assert status_of(db, case_id) == "ocr_done"


def test_review_before_ocr_finished_is_409(client, db, case_id):
    resp = review(client, case_id, 1, "approve")

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "'stage1_approve' is not allowed when case status is 'pending'; it requires 'ocr_done'"
    )
    assert status_of(db, case_id) == "pending"


def test_review_of_ocr_failed_case_is_409(client, db, case_id):
    finish_ocr(db, case_id, "ocr_failed")

    resp = review(client, case_id, 1, "approve")

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "'stage1_approve' is not allowed when case status is 'ocr_failed'; it requires 'ocr_done'"
    )
    assert status_of(db, case_id) == "ocr_failed"


def test_repeating_a_stage_decision_is_409(client, db, case_id):
    finish_ocr(db, case_id)
    assert review(client, case_id, 1, "approve").status_code == 200

    resp = review(client, case_id, 1, "approve")

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "'stage1_approve' is not allowed when case status is 'stage1_approved'; it requires 'ocr_done'"
    )
    assert status_of(db, case_id) == "stage1_approved"


def test_stage2_review_after_stage1_rejection_is_409(client, db, case_id):
    finish_ocr(db, case_id)
    assert review(client, case_id, 1, "reject").status_code == 200

    resp = review(client, case_id, 2, "approve")

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "'stage2_approve' is not allowed when case status is 'stage1_rejected'; it requires 'stage1_approved'"
    )
    assert status_of(db, case_id) == "stage1_rejected"


def test_concurrent_reviews_cannot_both_apply(db, case_id):
    """Two reviewers load the same ocr_done case; only the first decision is written."""
    finish_ocr(db, case_id)
    seen_by_both = db.cases.find_one({"_id": ObjectId(case_id)})

    transition_case(db, seen_by_both, "stage1_approve")
    with pytest.raises(InvalidTransition, match="changed from 'ocr_done' by another request"):
        transition_case(db, seen_by_both, "stage1_reject")

    assert status_of(db, case_id) == "stage1_approved"


@pytest.mark.parametrize("body", [{"stage": 3, "decision": "approve"}, {"stage": 1, "decision": "maybe"}, {}])
def test_malformed_review_request_is_422(client, case_id, body):
    assert client.post(f"/api/cases/{case_id}/review", json=body).status_code == 422


@pytest.mark.parametrize("case_id", [str(ObjectId()), "not-an-id"])
def test_review_of_unknown_case_is_404(client, case_id):
    assert review(client, case_id, 1, "approve").status_code == 404
