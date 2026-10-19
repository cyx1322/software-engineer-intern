"""Q1.1 batch import, validation, and case listing."""
import pytest
from bson import ObjectId

from app.db import transition_case

HEADER = b"case_ref,applicant_name,id_number,dob,issue_date,image_ref\n"


def test_sample_csv_import_reports_counts_and_reasons(upload, client, sample_csv):
    resp = upload(sample_csv, filename="account_applications.csv")

    assert resp.status_code == 201
    batch = resp.json()
    assert (batch["total_rows"], batch["accepted_count"], batch["rejected_count"]) == (50, 43, 7)
    # Row numbers are spreadsheet rows: the header is row 1.
    assert {r["row"]: (r["case_ref"], r["reasons"]) for r in batch["rejected_rows"]} == {
        6: ("APPL-2026-0005", ["dob '1975-13-12' is not a valid YYYY-MM-DD date"]),
        13: ("APPL-2026-0012", ["applicant_name is required"]),
        18: ("APPL-2026-0007", ["duplicate case_ref; first appears on row 8"]),
        19: ("APPL-2026-0018", ["dob '32/01/1980' is not a valid YYYY-MM-DD date"]),
        28: ("APPL-2026-0027", ["dob 'unknown' is not a valid YYYY-MM-DD date"]),
        41: ("APPL-2026-0040", ["applicant_name is required"]),
        48: ("APPL-2026-0031", ["duplicate case_ref; first appears on row 32"]),
    }
    assert batch["status_counts"]["pending"] == 43

    # The summary endpoint returns the same import result and current counts.
    stored = client.get(f"/api/batches/{batch['id']}").json()
    assert stored["rejected_rows"] == batch["rejected_rows"]
    assert stored["status_counts"] == {
        "pending": 43, "ocr_done": 0, "ocr_failed": 0, "stage1_approved": 0, "stage1_rejected": 0,
        "stage2_approved": 0, "stage2_rejected": 0, "exported": 0,
    }


def test_every_problem_in_a_row_is_reported(upload):
    csv = HEADER + (
        b"OK-1,Valid Person,TEST-ID-1,2000-01-01,2020-01-01,ok.jpg\n"
        b"BAD-1,,REAL-123,2000-02-30,2020-01-01,a.jpg\n"
        b"BAD-2,Issued Early,TEST-ID-2,2000-01-01,1999-12-31,b.jpg\n"
        b"BAD-3,Short Row,TEST-ID-3,2000-01-01\n"
        b"BAD-4,Long Row,TEST-ID-4,2000-01-01,2020-01-01,c.jpg,extra\n"
        b"BAD-5,Compact Date,TEST-ID-5,20000101,2020-01-01,d.jpg\n"
    )

    batch = upload(csv).json()

    assert batch["accepted_count"] == 1
    assert {r["case_ref"]: r["reasons"] for r in batch["rejected_rows"]} == {
        "BAD-1": [
            "applicant_name is required",
            "id_number must start with TEST-ID-",
            "dob '2000-02-30' is not a valid YYYY-MM-DD date",
        ],
        "BAD-2": ["issue_date must be after dob"],
        "BAD-3": ["issue_date is required", "image_ref is required"],
        "BAD-4": ["row has more values than the header"],
        "BAD-5": ["dob '20000101' is not a valid YYYY-MM-DD date"],
    }


def test_row_numbers_are_spreadsheet_rows_not_file_lines(upload):
    csv = HEADER + (
        b'OK-1,"Test\nApplicant",TEST-ID-1,2000-01-01,2020-01-01,ok.jpg\n'  # row 2, file lines 2-3
        b"BAD-1,,TEST-ID-2,2000-01-01,2020-01-01,a.jpg\n"                          # row 3
        b"\n"                                                                      # row 4, blank
        b"BAD-2,,TEST-ID-3,2000-01-01,2020-01-01,b.jpg\n"                          # row 5
    )

    batch = upload(csv).json()

    assert batch["total_rows"] == 3
    assert [(r["row"], r["case_ref"]) for r in batch["rejected_rows"]] == [(3, "BAD-1"), (5, "BAD-2")]


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "file is empty"),
        (b"case_ref,applicant_name,dob\nA,B,2000-01-01\n", "missing required columns: id_number, issue_date, image_ref"),
        (HEADER, "file has a header but no application rows"),
        ("case_ref,applicant_name\n".encode("utf-16"), "file must be a UTF-8 encoded CSV"),
    ],
)
def test_unreadable_file_is_rejected_with_reason(upload, content, message):
    resp = upload(content, filename="bad.csv")

    assert resp.status_code == 422
    assert resp.json()["detail"] == f"bad.csv: {message}"


def test_upload_without_file_is_rejected(client):
    assert client.post("/api/batches").status_code == 422


def test_list_cases_filters_by_status_and_paginates(upload, client, db, sample_csv):
    batch_id = upload(sample_csv).json()["id"]
    url = f"/api/batches/{batch_id}/cases"

    first = client.get(url, params={"page": 1, "page_size": 20}).json()
    last = client.get(url, params={"page": 3, "page_size": 20}).json()
    assert first["total"] == 43
    assert len(first["items"]) == 20 and len(last["items"]) == 3
    assert first["items"][0]["case_ref"] == "APPL-2026-0001"  # file order

    case = db.cases.find_one({"batch_id": ObjectId(batch_id), "case_ref": "APPL-2026-0010"})
    transition_case(db, case, "ocr_succeeded")
    done = client.get(url, params={"status": "ocr_done"}).json()
    assert done["total"] == 1 and done["items"][0]["case_ref"] == "APPL-2026-0010"
    assert client.get(url, params={"status": "pending"}).json()["total"] == 42


@pytest.mark.parametrize("params", [{"status": "approved"}, {"page": 0}, {"page_size": 101}])
def test_list_cases_rejects_bad_query(upload, client, sample_csv, params):
    batch_id = upload(sample_csv).json()["id"]
    assert client.get(f"/api/batches/{batch_id}/cases", params=params).status_code == 422


@pytest.mark.parametrize("batch_id", [str(ObjectId()), "not-an-id"])
def test_unknown_batch_is_404(client, batch_id):
    assert client.get(f"/api/batches/{batch_id}").status_code == 404
    assert client.get(f"/api/batches/{batch_id}/cases").status_code == 404
