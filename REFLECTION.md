# Implementation reflection

Complete each section before submission. Replace bracketed prompts with decisions, measurements, or evidence. Use `Not completed` or `Not measured` where applicable, and explain the gap. Reviewers assess this record alongside the code.

## 1. Design decisions

| Decision | Rationale |
|---|---|
| Database | MongoDB 8 through `pymongo`, as the exercise recommends. A case is one document that gains OCR fields in Module 2, so a schemaless document store fits without migrations. `find_one_and_update` gives an atomic compare-and-set on `status`, which is all the review workflow needs, so a standalone server works without multi-document transactions. Tests use `mongomock` so they pass without a server, and the same suite runs against real MongoDB with `TEST_MONGO_URL`. The schema is in [q1_api/README.md](q1_api/README.md#mongodb-schema). |
| Background OCR processing | `POST /api/batches/{id}/process` puts the batch's `pending` case ids on an in-process queue, read by `OCR_WORKERS` (4) daemon threads in `q1_api/app/ocr.py`, and returns `202` at once. Each worker calls OCR with retries, then writes the result and the new status (`ocr_done` or `ocr_failed`) in one compare-and-set update. There is no intermediate "processing" status, so a crash cannot leave a case stuck: an unfinished case is still `pending`, and calling `process` again queues it. OCR has no side effects and returns the same fields for the same image, so repeating an attempt is harmless. Threads were chosen over a task queue such as Celery because the layout allows no second application service, and the existing `pymongo` code is synchronous. The cost is that the `processing` flag and duplicate-run guard are per process, which assumes a single API process. |
| Review state machine | `q1_api/app/state_machine.py` owns the rules. `TRANSITIONS` maps `(current status, event)` to the next status, and `next_status()` is the only lookup. A status with no outgoing entry is terminal, so rejected cases cannot reach stage 2 or export. `db.transition_case()` applies a transition with an update filtered on the status it checked, so a concurrent second decision gets `409` instead of overwriting the first. Adding a workflow step, such as returning a case to stage 1, means a new table entry and an endpoint that sends the new event. |
| Delivery idempotency key | The key is the export filename, `EXPORT-<export _id>.csv`, set in `q1_api/app/export.py`. The export id is chosen once, before any case is claimed, and is stored on every claimed case (`export_id`) and in the export document. Every attempt reads the key from the stored document, so retries after a `500`, after a lost response, through `POST /api/exports/{id}/deliver`, or after a restart all send the same key. A new export has a new ObjectId, so it always gets a new key. I did not use a content hash: two different exports could contain identical bytes, for example if a case were returned to stage 1 and approved again, and the service would silently drop the second one as a duplicate. |

## 2. Mock observations and retry configuration

| Item | Measurement or decision |
|---|---|
| Observed OCR failure rates | 1,093 sequential attempts against `mock_ocr` with default settings (seed 42, `OCR_FAIL_RATE=0.20`, `OCR_HANG_RATE=0.10`, `OCR_HANG_SECONDS=600`). Of the 1,038 attempts on readable images, 69.8% returned `ok`, 18.4% returned HTTP `500`, and 11.8% hung with no response within the 2-second client timeout. All 55 attempts on the five `badscan` images returned HTTP `422`. Malformed requests always returned HTTP `400`, and well-formed requests never did. The method and full counts are in [Measurement details](#measurement-details). |
| Observed account-opening behavior | 597 sequential ingestion attempts against `mock_onboarding` with default settings (seed 7, `ONBOARDING_FAIL_RATE=0.20`, `ONBOARDING_LOST_RATE=0.15`, `ONBOARDING_LOST_SECONDS=120`). Of the 517 attempts with a key that had not been ingested, 61.9% returned a receipt, 22.6% returned HTTP `500`, and 15.5% were lost: ingested, but with no response within 2 seconds. All 80 retries after a lost response returned the original receipt with `"deduplicated": true`. 400 keys ended with exactly 400 deliveries. |
| OCR timeout | A 5-second read timeout and a 2-second connect timeout per attempt (`OCR_TIMEOUT_SECONDS`). Every healthy OCR response in the measurements arrived within 23 ms, and a hang gives no response for 600 seconds. That leaves no overlap, so any timeout from about 1 second upward tells the two apart. 5 seconds allows about 200 times the slowest observed response, as margin for container networking or a slower real vendor, while capping each hang at 5 seconds of worker time. The account-opening client uses the same 5 seconds: receipts arrived within 24 ms, and a lost response is delayed by 120 seconds. |
| Retry limit | 6 attempts per case (`OCR_MAX_ATTEMPTS`), including the first. The wait before retry *n* is 0.5 × 2^(n−1) seconds, capped at 8 seconds and scaled by a random 50–100%: at most 0.5, 1, 2, 4, and 8 seconds. One case therefore takes at most 6 × 5 s + 15.5 s ≈ 46 seconds. 30.2% of measured attempts on readable images failed (a `500` or a hang). If attempts are independent, 6 attempts leave about 0.08% of cases (0.302^6) with no result, which is about one batch in 36 with any such case. Fewer attempts would raise that to 0.8% of cases for 4 attempts. More attempts would mostly add worst-case time. A `422` or another `4xx` is never retried. Delivery uses the same limit and backoff, reusing the same key each time. In the clean run, no case needed more than 5 attempts. |
| Exhausted retries | The case is set to `ocr_failed` with `ocr_error = {reason: "no result after 6 attempts; last error: no response within 5 s", retryable: true, attempts: 6}`, and the API logs a warning for the case. Staff find these cases with `GET /api/batches/{id}/cases?status=ocr_failed`. `retryable` separates them from unreadable scans, which have `retryable: false` and the OCR service's reason. The next action is `POST /api/batches/{id}/process`, which moves retryable failures back to `pending` (the `ocr_retry` transition) and processes them again. If they keep failing, the OCR service is down, which the warnings in the API log show. The last error separates a slow service (`no response within 5 s`) from an unreachable one (`could not connect within 2 s`, or `Connection refused`). For delivery, an export whose attempts run out keeps its cases and gets `delivery.status: failed` with the last error. `GET /api/batches/{id}` lists it, and `POST /api/exports/{id}/deliver` retries it with the same key. A redeliver that overlaps a running delivery is safe: each call adds its attempts with `$inc`, and the result is only written while the status is not `delivered`. |

### Measurement details

**Method.** Measured on 2026-10-18 on macOS with Python 3.12. Both mocks were started from the repository root with their default configuration. `mock_onboarding` ran on port 9201 because another local service already used 9200; the port does not change the mock's behavior. A probe script sent one request at a time with `httpx` and a 2-second client timeout. Sending requests one at a time keeps the seeded outcome sequence reproducible. A request with no response within 2 seconds was counted as a hang (OCR) or a lost response (account opening). Every healthy response arrived within 25 ms, and the injected delays are 600 s and 120 s, so 2 seconds separates the two cases with no overlap. The client-side OCR counts were checked against `GET /debug/requests` and matched exactly: 725 `ok`, 191 `error_500`, 122 `hang`, and 55 `unreadable`, 1,093 records in total.

**OCR results.** The 95% intervals are Wilson score intervals.

| Run | Attempts | `ok` | HTTP `500` | Hang | HTTP `422` |
|---|---|---|---|---|---|
| A: one attempt for each of the 43 accepted sample cases, in file order, on a fresh mock | 43 | 25 | 9 | 4 | 5 |
| B: 1,000 attempts cycling through the 38 readable accepted images | 1,000 | 700 (70.0%, CI 67.1–72.8%) | 182 (18.2%, CI 15.9–20.7%) | 118 (11.8%, CI 9.9–13.9%) | 0 |
| C: each of the five `badscan` images 10 times | 50 | 0 | 0 | 0 | 50 |
| Total | 1,093 | 725 | 191 | 122 | 55 |

- **HTTP `400`.** An invalid JSON body, an empty body, a missing `case_id`, a missing `image_ref`, and a JSON array each returned `400` with `need JSON body {case_id, image_ref}`. None of them appeared in `/debug/requests`.
- **HTTP `422`.** Unreadable scans fail on every attempt with a reason such as `cannot read identity-card fields from 'id_badscan_0044.jpg' (scan quality)`. They make up 5 of the 50 CSV rows (10%) and 5 of the 43 accepted cases (11.6%).
- **Delayed responses.** A hang sends nothing until `OCR_HANG_SECONDS` has passed. On a separate instance with `OCR_HANG_SECONDS=3` and `OCR_HANG_RATE=1`, a hung request returned HTTP `200` after 3.0 seconds with the same fields as a normal response.
- **Deterministic fields.** Across the 700 `ok` responses in run B, each `image_ref` always returned identical fields.
- **Latency on localhost.** `ok` responses took 1.5 ms at the median, 7.1 ms at p99, and 23 ms at most. The slowest HTTP `500` took 9 ms, and the slowest `422` took 1.2 ms.
- **Repeated failures.** On readable images, 30.2% of attempts failed with a `500` or a hang. If attempts are independent, the chance that every attempt fails is 2.8% for 3 attempts, 0.8% for 4, and 0.25% for 5.

**Account-opening results.**

| Attempts | Count | Receipt (HTTP `200`) | HTTP `500` | Lost |
|---|---|---|---|---|
| First attempt for each of 400 new keys | 400 | 247 (61.8%) | 93 (23.2%) | 60 (15.0%) |
| All attempts with a key not yet ingested | 517 | 320 (61.9%, CI 57.6–66.0%) | 117 (22.6%, CI 19.2–26.4%) | 80 (15.5%, CI 12.6–18.8%) |
| Retries with a key already ingested | 80 | 80, all with `"deduplicated": true` | 0 | 0 |

- **HTTP `400`.** A missing or empty `Idempotency-Key` returned `400` with `missing Idempotency-Key header`, and no delivery was recorded.
- **Lost responses are ingested.** After the first attempts, `/debug/deliveries` held 307 records: the 247 receipts plus the 60 lost responses. On a separate instance with `ONBOARDING_LOST_RATE=1` and `ONBOARDING_LOST_SECONDS=3`, the delivery appeared in `/debug/deliveries` one second into the delay, and the receipt arrived after 3.0 seconds.
- **Retries with the same key.** Each key without a receipt was retried with the same key until it got one. Every key got a receipt within five attempts: 247 needed 1 attempt, 119 needed 2, 27 needed 3, 4 needed 4, and 3 needed 5. The run ended with 400 deliveries for 400 keys, and no key was ingested twice. The receipt's `attempts` field counts deduplicated retries but not `500` responses, so 80 receipts show `attempts: 2` and the other 320 show `attempts: 1`.
- **Same key, different bytes.** This returned the original receipt with the original `sha256` and `"deduplicated": true`, and no delivery was added.
- **Latency on localhost.** Receipts took 1.6 ms at the median, 6.9 ms at p99, and 24 ms at most. The slowest HTTP `500` took 9.5 ms.

**Configured and observed rates.** Every configured probability falls inside the 95% interval of the observed rate. The seed reproduces outcomes only for the same request sequence. With concurrent workers, which case gets which outcome changes, but the rates do not.

**Mock log noise.** The mock terminals print `BrokenPipeError` tracebacks about 600 seconds (OCR) or 120 seconds (account opening) after a client timeout. The mock is writing the delayed response to a connection the client has already closed. The server keeps running, and an account-opening delivery is recorded before the delay starts.

## 3. Verification evidence

Provide command output, excerpts, or links to artifacts for each check.

### OCR recovery

**Setup.** On 2026-10-19 I restarted `mock_ocr` with its defaults (seed 42) and ran the API with `uvicorn` against a local MongoDB, with 4 workers. Each run imported `data/account_applications.csv`, which gives 43 accepted cases. I reset `/debug/requests` before each run.

**Clean run (batch A).** `POST /process` returned `202` in 11 ms with `queued: 43`. `GET /api/batches/{id}` showed progress, for example `{'pending': 25, 'ocr_done': 15, 'ocr_failed': 3}` after 6 seconds. The run finished after 22 seconds with 38 `ocr_done` and 5 `ocr_failed`. `/debug/requests` recorded 67 attempts: 38 `ok`, 14 `error_500`, 10 `hang`, and 5 `unreadable`. The 5 failures are the `badscan` images, each stored with the service's reason, such as `cannot read identity-card fields from 'id_badscan_0009.jpg' (scan quality)`.

**Interrupted run (batch B).** I started processing, waited until 21 cases had finished, and killed the API process with `kill -9`. MongoDB then held 18 `ocr_done`, 3 `ocr_failed`, and 22 `pending` cases, and no case in any other status. After a restart, `GET` showed the same counts with `processing: false`. A second `POST /process` returned `queued: 22` and finished 12 seconds later. `/debug/requests` recorded 31 attempts before the kill and 30 after it.

Four cases were mid-retry when the process was killed. They stayed `pending` and finished after the restart. Two of their records from `/debug/requests`, with the time relative to the kill:

```text
{"seq": 9,  "case_id": "APPL-2026-0010", "outcome": "hang",      "ts": 1792386529.876}   # -6.3 s
{"seq": 20, "case_id": "APPL-2026-0010", "outcome": "hang",      "ts": 1792386535.155}   # -1.0 s
{"seq": 30, "case_id": "APPL-2026-0030", "outcome": "error_500", "ts": 1792386535.704}   # -0.5 s
{"seq": 31, "case_id": "APPL-2026-0030", "outcome": "error_500", "ts": 1792386536.054}   # -0.1 s
--- kill -9, restart, POST /process ---
{"seq": 32, "case_id": "APPL-2026-0010", "outcome": "error_500", "ts": 1792386558.498}   # +22.3 s
{"seq": 33, "case_id": "APPL-2026-0030", "outcome": "ok",        "ts": 1792386558.500}   # +22.3 s
{"seq": 48, "case_id": "APPL-2026-0010", "outcome": "ok",        "ts": 1792386558.790}   # +22.6 s
```

The other two, `APPL-2026-0023` and `APPL-2026-0028`, each had a `hang` before the kill and an `ok` after it. No case that finished before the kill was sent to OCR again.

**Result.** I compared batch B with batch A case by case on `status`, the stored `ocr` fields, and `ocr_error.reason`: `43 vs 43 cases; 0 differ`. Re-processing after a crash gives the same final state as a clean run.

### Account-opening submission

**Setup.** `mock_onboarding` ran with its defaults (seed 7, 20% `500`, 15% lost responses delayed 120 seconds) on port 9201, with `ONBOARDING_URL=http://localhost:9201/ingest`. `/debug/deliveries` was reset first. All calls went through the API.

**Batch A.** In stage 1, 37 cases were approved and 1 was rejected. A stage 2 review of the rejected case returned `409`: `'stage2_approve' is not allowed when case status is 'stage1_rejected'; it requires 'stage1_approved'`. In stage 2, 34 cases were approved, 1 was rejected, and 2 were held at `stage1_approved`.
- Export 1 contained exactly the 34 `stage2_approved` cases and was delivered on the first attempt (`DLV-0001`).
- An immediate second export returned `409`: `batch has no stage2_approved cases to export`.
- After one held case was approved, export 2 contained only that case. Its first attempt returned a `500`, and the retry with the same key was ingested (`DLV-0002`, `attempts: 2` on the export). The two exports had no case in common.

**Batch B.** Ten exports of one case each were made with default failure rates until lost responses had appeared. Three attempts timed out after the mock had ingested the file. The API log shows each retry with the same key:

```text
WARNING app.retry: delivery EXPORT-6ad5a62d194e910fd65fede0.csv: attempt 1/6 failed (no response within 5 s); retrying in 0.3 s
INFO httpx: HTTP Request: POST http://localhost:9201/ingest "HTTP/1.0 200 OK"
```

Each retry received the original receipt with `"deduplicated": true`. One more export had a `500` followed by a successful retry.

**`/debug/deliveries`.** There were 12 records for 12 exports. No `idempotency_key` appeared twice, and every export key appeared once. The three lost responses show `attempts: 2`, which counts the deduplicated retry, under a single `delivery_id`:

```json
{"delivery_id": "DLV-0001", "idempotency_key": "EXPORT-6ad5a62d194e910fd65fedda.csv", "file_name": "EXPORT-6ad5a62d194e910fd65fedda.csv", "sha256": "14012e54cd4fbd2a754ee8eb4a56873be661ea71c52c581acda2e3ac23dda023", "attempts": 1}
{"delivery_id": "DLV-0006", "idempotency_key": "EXPORT-6ad5a62d194e910fd65fede0.csv", "file_name": "EXPORT-6ad5a62d194e910fd65fede0.csv", "sha256": "a308a680e08eb1c050905653c9909bd01be6bd522db4b85304a0cb5ff2044184", "attempts": 2}
```

**Result.** I checked every export document in MongoDB against `/debug/deliveries`. Each export was ingested once. Its stored `sha256` matched the ingested hash, and its stored receipt had the same `delivery_id`. 45 cases were exported across the 12 exports, and the export sizes also sum to 45, so no case was in two exports.

### Backup retention

[Include before-and-after file listings showing that only the seven most recent backups remain.]

## 4. AI-generated code requiring further review

[Identify AI-generated code you do not fully understand. Include file paths, the behavior in question, and the review still needed. Enter `None` if applicable.]

The assessment values accurate disclosure. The requirement to explain submitted code during the interview remains in effect.

## 5. Incomplete work and next steps

**Module 2 limitations**

- The OCR workers and the `processing` flag live in the API process, which assumes a single API process. With several processes, results stay correct because every write is a compare-and-set, but a batch could be sent to OCR twice and `processing` would only reflect one process.
- After the API restarts, an export whose delivery was in progress keeps `delivery.status: pending` until someone calls `POST /api/exports/{id}/deliver`. Nothing redelivers it automatically.
- An export call with no new `stage2_approved` cases returns `409` instead of creating an empty file, so nothing empty is delivered.
- With 6 attempts, about 0.08% of readable cases are expected to run out of retries, which is about one batch in 36. Those cases are recoverable by processing the batch again, but until then a clean run and an interrupted run can differ on them.

**Module 3** is not started.
