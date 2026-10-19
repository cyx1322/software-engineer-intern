"""Q2.1 and Q2.2: background OCR processing, retries, and recovery."""
import json
import threading
import time

import httpx
import pytest

from app import config

CSV = (
    b"case_ref,applicant_name,id_number,dob,issue_date,image_ref\n"
    b"APPL-1,Test One,TEST-ID-1,1990-01-01,2020-01-01,id_scan_0001.jpg\n"
    b"APPL-2,Test Two,TEST-ID-2,1990-01-01,2020-01-01,id_badscan_0002.jpg\n"
    b"APPL-3,Test Three,TEST-ID-3,1990-01-01,2020-01-01,id_scan_0003.jpg\n"
)
FIELDS = {
    "id_number": "TEST-ID-830454",
    "full_name": "Test Applicant 0454",
    "dob": "1969-11-19",
    "issue_date": "2020-06-06",
    "confidence": 0.94,
}
UNREADABLE = "cannot read identity-card fields from 'id_badscan_0002.jpg' (scan quality)"


class FakeOcr:
    """Behaves like mock_ocr. Images containing `badscan` get a 422. Other images
    follow their script of outcomes ("500", "hang", "crash") and then succeed."""

    def __init__(self, script=None):
        self.script = script or {}
        self.requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if "badscan" in body["image_ref"]:
            return httpx.Response(422, json={"error": UNREADABLE})
        outcomes = self.script.get(body["image_ref"], [])
        outcome = outcomes.pop(0) if outcomes else "ok"
        if outcome == "500":
            return httpx.Response(500, json={"error": "internal OCR engine error (transient)"})
        if outcome == "hang":
            raise httpx.ReadTimeout("timed out", request=request)
        if outcome == "crash":  # the worker dies in the middle of this case
            raise RuntimeError("worker stopped")
        return httpx.Response(200, json=FIELDS)

    def attempts(self, image_ref):
        return sum(body["image_ref"] == image_ref for body in self.requests)


def process(client, batch_id):
    resp = client.post(f"/api/batches/{batch_id}/process")
    assert resp.status_code == 202
    return resp.json()


def wait_for_ocr(client, batch_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while (batch := client.get(f"/api/batches/{batch_id}").json())["processing"]:
        assert time.monotonic() < deadline, "OCR processing did not finish"
        time.sleep(0.01)
    return batch


def cases_by_ref(client, batch_id):
    return {c["case_ref"]: c for c in client.get(f"/api/batches/{batch_id}/cases").json()["items"]}


@pytest.fixture
def batch_id(upload):
    return upload(CSV).json()["id"]


def test_process_returns_before_ocr_finishes(client, fake_ocr, batch_id):
    released = threading.Event()
    fake = FakeOcr()

    def slow_ocr(request):
        released.wait(5)
        return fake(request)

    fake_ocr(slow_ocr)

    started = process(client, batch_id)
    assert started["queued"] == 3 and started["processing"] is True
    assert client.get(f"/api/batches/{batch_id}").json()["status_counts"]["pending"] == 3

    released.set()
    finished = wait_for_ocr(client, batch_id)
    assert finished["status_counts"]["ocr_done"] == 2
    assert finished["status_counts"]["ocr_failed"] == 1


def test_results_and_failure_reasons_are_stored(client, fake_ocr, batch_id):
    fake = FakeOcr({"id_scan_0003.jpg": ["500", "hang"]})
    fake_ocr(fake)

    process(client, batch_id)
    wait_for_ocr(client, batch_id)
    cases = cases_by_ref(client, batch_id)

    assert cases["APPL-1"]["status"] == "ocr_done"
    assert cases["APPL-1"]["ocr"] == FIELDS and cases["APPL-1"]["ocr_error"] is None
    # A 500 and a timeout are retried; the third attempt succeeds.
    assert cases["APPL-3"]["status"] == "ocr_done" and fake.attempts("id_scan_0003.jpg") == 3
    # A 422 is not retried, and the service's reason is stored unchanged.
    assert cases["APPL-2"]["status"] == "ocr_failed" and fake.attempts("id_badscan_0002.jpg") == 1
    assert cases["APPL-2"]["ocr_error"] == {"reason": UNREADABLE, "retryable": False, "attempts": 1}

    # Every request matches the OCR contract, so the service never has a reason to return 400.
    refs = {"id_scan_0001.jpg": "APPL-1", "id_badscan_0002.jpg": "APPL-2", "id_scan_0003.jpg": "APPL-3"}
    assert all(body == {"case_id": refs[body["image_ref"]], "image_ref": body["image_ref"]} for body in fake.requests)

    # Staff find failed cases, with their reasons, through the status filter.
    failed = client.get(f"/api/batches/{batch_id}/cases", params={"status": "ocr_failed"}).json()["items"]
    assert [(c["case_ref"], c["ocr_error"]["reason"]) for c in failed] == [("APPL-2", UNREADABLE)]


def test_exhausted_retries_are_recorded_and_retried_on_the_next_run(client, fake_ocr, batch_id):
    attempts = config.OCR_MAX_ATTEMPTS
    fake = FakeOcr({"id_scan_0001.jpg": ["hang"] * attempts})
    fake_ocr(fake)

    process(client, batch_id)
    wait_for_ocr(client, batch_id)

    failed = cases_by_ref(client, batch_id)["APPL-1"]
    assert failed["status"] == "ocr_failed" and fake.attempts("id_scan_0001.jpg") == attempts
    assert failed["ocr_error"] == {
        "reason": f"no result after {attempts} attempts; last error: no response within 5 s",
        "retryable": True,
        "attempts": attempts,
    }

    # Processing again re-queues the transient failure only, not the unreadable scan.
    assert process(client, batch_id)["queued"] == 1
    wait_for_ocr(client, batch_id)
    retried = cases_by_ref(client, batch_id)["APPL-1"]
    assert retried["status"] == "ocr_done"
    assert retried["ocr"] == FIELDS and retried["ocr_error"] is None  # same as a first-time success
    assert fake.attempts("id_badscan_0002.jpg") == 1


def test_case_interrupted_mid_ocr_stays_pending_and_is_processed_again(client, fake_ocr, batch_id):
    fake = FakeOcr({"id_scan_0001.jpg": ["crash"]})
    fake_ocr(fake)

    process(client, batch_id)
    batch = wait_for_ocr(client, batch_id)
    assert batch["status_counts"]["pending"] == 1
    assert cases_by_ref(client, batch_id)["APPL-1"]["status"] == "pending"

    assert process(client, batch_id)["queued"] == 1  # only the interrupted case
    wait_for_ocr(client, batch_id)
    cases = cases_by_ref(client, batch_id)
    assert cases["APPL-1"]["status"] == "ocr_done" and cases["APPL-1"]["ocr"] == FIELDS
    assert fake.attempts("id_scan_0003.jpg") == 1  # finished cases are not sent again

