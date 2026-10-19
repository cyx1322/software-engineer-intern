# Module 2: OCR integration and export delivery

Add OCR, export, and delivery modules to `q1_api/app/`. Register the new endpoints on the existing `app.main:app` application. Keep the [fixed repository layout](../README.md#2-fixed-structure-and-naming).

Read the [Module 2 requirements](../README.md#4-module-2-ocr-integration-and-export-delivery-35-points), [OCR contract](../mock_ocr/README.md), and [account-opening contract](../mock_onboarding/README.md) before implementing the clients.

The implementation is in `q1_api/app/`: `ocr.py` (background OCR), `export.py` (export and delivery), `retry.py` (timeouts and retries for both services), `exports.py` (export endpoints), and `config.py` (settings). The MongoDB schema, including the Module 2 fields and the `exports` collection, is in [q1_api/README.md](../q1_api/README.md#mongodb-schema).

## Configuration

The API reads its settings from environment variables when it starts. Set them in the shell before starting `uvicorn`, or in the Compose environment. The defaults match the local setup below.

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `identity_verification` | Database name |
| `SEED_CSV` | unset | CSV imported once at startup as a seed batch. Compose sets it to the sample file. |
| `READY_TIMEOUT_SECONDS` | `2` | Time limit for the MongoDB ping behind `/api/ready` |
| `OCR_URL` | `http://localhost:9100/ocr/parse` | OCR parse endpoint |
| `OCR_TIMEOUT_SECONDS` | `5` | Read timeout for one OCR attempt. The connect timeout is 2 seconds. |
| `OCR_MAX_ATTEMPTS` | `6` | OCR attempts per case, including the first |
| `OCR_WORKERS` | `4` | Worker threads that call OCR in parallel |
| `ONBOARDING_URL` | `http://localhost:9200/ingest` | Account-opening ingestion endpoint |
| `ONBOARDING_TIMEOUT_SECONDS` | `5` | Read timeout for one delivery attempt |
| `ONBOARDING_MAX_ATTEMPTS` | `6` | Delivery attempts per export request, including the first |
| `RETRY_BACKOFF_BASE_SECONDS` | `0.5` | Wait before the first retry. Each later wait doubles. |
| `RETRY_BACKOFF_MAX_SECONDS` | `8` | Longest wait between attempts |

Each wait is scaled by a random factor between 50% and 100%, so parallel workers do not retry in step. [REFLECTION.md](../REFLECTION.md#2-mock-observations-and-retry-configuration) explains how the timeout and retry values follow from the measured mock behavior.

## Run without Docker

Start MongoDB, then start each service in a separate terminal from the repository root:

```bash
python3 mock_ocr/server.py
```

```bash
python3 mock_onboarding/server.py
```

```bash
cd q1_api
uvicorn app.main:app --reload
```

If a port is already taken, start the mock on another port and point the API at it. For example, use `python3 mock_onboarding/server.py --port 9201`, then start the API with:

```bash
ONBOARDING_URL=http://localhost:9201/ingest uvicorn app.main:app
```

## Drive one batch

```bash
curl -sS -F "file=@data/account_applications.csv" http://localhost:8000/api/batches
curl -sS -X POST http://localhost:8000/api/batches/<batch_id>/process
curl -sS http://localhost:8000/api/batches/<batch_id>
curl -sS "http://localhost:8000/api/batches/<batch_id>/cases?status=ocr_failed"
curl -sS -X POST http://localhost:8000/api/cases/<case_id>/review -H 'Content-Type: application/json' -d '{"stage": 1, "decision": "approve"}'
curl -sS -X POST http://localhost:8000/api/cases/<case_id>/review -H 'Content-Type: application/json' -d '{"stage": 2, "decision": "approve"}'
curl -sS -X POST http://localhost:8000/api/batches/<batch_id>/export
curl -sS http://localhost:8000/api/exports/<export_id>/file
```

| Endpoint | Behavior |
|---|---|
| `POST /api/batches/{id}/process` | Returns `202` immediately with the batch status and `queued`, the number of cases sent to the workers. It queues every `pending` case, plus `ocr_failed` cases whose retries ran out. If the batch is already being processed, it queues nothing. |
| `GET /api/batches/{id}` | Includes `processing` (true while this batch's OCR work is queued or running) and `status_counts`, which change as cases finish. It also lists the batch's `exports` with their delivery status. |
| `POST /api/batches/{id}/export` | Claims every `stage2_approved` case into a new export, delivers it, and returns `201` with the export and its `delivery`. Returns `409` if there are no `stage2_approved` cases, for example when nothing new was approved since the last export. |
| `POST /api/exports/{id}/deliver` | Delivers an export again with its original key. Use it after `delivery.status` is `failed`, or when the API stopped during a delivery. It does nothing for a delivered export. It is safe to call while another delivery of the same export is still retrying: `attempts` counts both calls, and a failure that finishes later never replaces a delivered result. |
| `GET /api/exports/{id}/file` | Downloads the export CSV |

## Error handling

The OCR and account-opening clients share `post_with_retries()` in `app/retry.py`. Each attempt has an explicit timeout. A `5xx`, a timeout, or a connection error is retried after a backoff, up to the attempt limit. A `4xx` is not retried, because sending the same request again gets the same answer.

The stored error says which kind of failure the last attempt hit, so staff can tell a slow service from an unreachable one:

| Last error | Meaning |
|---|---|
| `no response within 5 s` | Connected, but the service did not answer in time: a hang or a lost response |
| `could not connect within 2 s` | No connection was established, for example a host that is down or a network problem |
| `request failed: ConnectError('[Errno 61] Connection refused')` | Nothing is listening at the URL; check that the service is running and that the URL is configured correctly |
| `HTTP 500: <service message>` | The service answered with a transient error |

| Outcome | OCR service | Account-opening service |
|---|---|---|
| HTTP `400` | Requests always send `{"case_id": <case_ref>, "image_ref": <image_ref>}` as JSON, as the contract requires; a test checks every request body. If a `400` ever came back, the case would be set to `ocr_failed` with the service's message, without a retry. | Every attempt sends `Idempotency-Key` and `X-File-Name`, so the service has no reason to return a `400`. If one came back, `delivery.status` would be `failed` with the message. |
| HTTP `500` | Retried within `OCR_MAX_ATTEMPTS` and the timeout. | Retried with the same `Idempotency-Key`. |
| Delayed response | The attempt times out after `OCR_TIMEOUT_SECONDS` and is retried. The result does not change between attempts, so a retry is safe. | The attempt times out and is retried with the same key. The service has already ingested the file, so it returns the original receipt with `"deduplicated": true` and does not ingest it again. |
| HTTP `422` | Not retried. The case is set to `ocr_failed`, and the service's reason is stored in `ocr_error.reason` without changes. The case list returns that reason. | Not part of the contract |
| Attempts used up | `ocr_failed` with `ocr_error.retryable: true` and a reason such as `no result after 6 attempts; last error: no response within 5 s`. Processing the batch again retries these cases. | `delivery.status: failed` with the last error. The cases stay in this export. `POST /api/exports/{id}/deliver` retries with the same key. |

## Recovery

- **API stopped during OCR.** A case stays `pending` until its result is written in one atomic update, so no case is left in an intermediate status. Call `POST /api/batches/{id}/process` again. It queues only the cases that are still `pending`, plus transient failures. Finished cases are not sent to OCR again.
- **API stopped during an export.** If the process stops after claiming cases but before writing the export document, the next `POST /api/batches/{id}/export` rebuilds the export under the same id, filename, and key. If the process stops during delivery, `POST /api/exports/{id}/deliver` sends it again with the same key.

The background workers are threads inside the API process. This assumes a single API process, which is how the API runs locally and in Compose. With several API processes, each one would track its own `processing` flag. The results would still be correct, because every write is a compare-and-set on the case status, but cases might be sent to OCR twice.

## Verification

[REFLECTION.md](../REFLECTION.md#3-verification-evidence) records one batch driven through import, background OCR, both review stages, export, and delivery against both mocks. It also records the comparison between a clean run and a run interrupted with `kill -9` and then processed again.
