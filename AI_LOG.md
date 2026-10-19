# AI interaction record

Record interactions that influenced architecture, implementation decisions, or generated code. Include prompt excerpts, conversation links, or exported records. Routine lookups do not require entries.

Complete one entry per relevant interaction using the template below. Replace bracketed prompts and add entries as needed. If AI did not materially influence the code, state that and remove the blank entry.

## Interaction 1: Module 1 core API

| Field | Description |
|---|---|
| Tool or model | Claude Code (Claude Opus 5.5) |
| Prompt or record | I asked Claude to read the exercise instructions and implement Module 1. I set the key decisions: MongoDB as the database, a FastAPI multipart upload for the CSV, and a clearly documented MongoDB schema. I also chose the test coverage: invalid CSV rows with counts and rejection reasons, the successful case lifecycle, and four invalid transitions. These were a stage 2 review before stage 1 finishes, a review before OCR finishes, a repeated decision, and a stage 2 review after a stage 1 rejection. |
| Affected files | `q1_api/app/*.py`, `q1_api/tests/*.py`, `q1_api/README.md`, `q1_api/pytest.ini`, `requirements.txt`, `.gitignore`, `REFLECTION.md` |

### Adopted output

Claude generated the Module 1 implementation: CSV validation (`validation.py`), the transition table (`state_machine.py`), MongoDB access with an atomic status update (`db.py`), the batch and review routers, response models, the pytest suite with a mongomock fixture, and the schema documentation in `q1_api/README.md`. It also added `pythonpath = .` to `pytest.ini`, because the scaffold test could not import `app` when run from the repository root.

### Changes and rejected output

I reviewed the generated code and found two problems. Claude fixed both at my request.

- **Row numbers drifted after multi-line values.** `validate_csv()` took each row number from `reader.line_num`, which counts physical lines in the file. A quoted value with an embedded line break spans two lines but fills only one spreadsheet row. So every row below it was numbered one higher than the row staff would see. The docstring and the README both describe the number as the spreadsheet row, so the code disagreed with its own documentation. Claude changed the validator to count CSV records, starting from the header as row 1 and counting blank lines. It also added `test_row_numbers_are_spreadsheet_rows_not_file_lines`. Parse errors for malformed CSV still give the file line, since that is what an editor displays.
- **One of the exercise's 409 examples was untested.** Reviewing an `ocr_failed` case already returned 409. The exercise names this case as an example, but the suite had no test for it. Claude added `test_review_of_ocr_failed_case_is_409`, which checks the reason and that the status does not change.

## Interaction 2: Mock measurements and Module 2 integration

| Field | Description |
|---|---|
| Tool or model | Claude Code (Claude Opus 5.5) |
| Prompt or record | First, I asked Claude to start both mock services locally and measure how often each returns a 400, a 500, a delayed response, a 422, and a success, then record the method and results in REFLECTION.md. Next, I asked it to implement Module 2 with this error handling: build every request to match the documented contract so a 400 never occurs; retry OCR 500s within the timeout and retry limits; retry account-opening 500s with the same export key; retry delayed responses, keeping the same export key for account opening; and for an OCR 422, store the service's reason and return it through the API. |
| Affected files | `q1_api/app/config.py`, `retry.py`, `ocr.py`, `export.py`, `exports.py`, and changes to `batches.py`, `db.py`, `schemas.py`, `state_machine.py`, `main.py`; `q1_api/tests/conftest.py`, `test_ocr.py`, `test_export.py`; `q1_api/README.md`, `q2_integration/README.md`, `REFLECTION.md` |

### Adopted output

Claude wrote probe scripts, not committed, that measured both mocks and produced the tables in REFLECTION.md section 2. It then generated the Module 2 implementation:
- In-process OCR worker threads that leave a case `pending` until its result is written, so a crash leaves nothing in an intermediate status.
- A shared retry helper with explicit timeouts, exponential backoff with jitter, and no retries on `4xx`.
- An `ocr_retry` transition that re-queues cases whose retries ran out.
- Exports that claim their cases with compare-and-set updates and derive the idempotency key from the export id, plus a redelivery endpoint.
- Tests that use fake services through `httpx.MockTransport`.

Claude also ran the clean-run and `kill -9` comparison and the delivery checks against the real mocks, and wrote the evidence in REFLECTION.md section 3.

### Changes and rejected output

I reviewed the generated code and found two problems. Claude fixed both at my request.

- **An overlapping redeliver could undo a successful delivery.** `deliver()` replaced the whole `delivery` object without checking its current status, and computed `attempts` from a value it had read before sending. If staff called the redeliver endpoint while the first delivery was still retrying, which can take about 46 seconds, the call that finished last won. A failure finishing after a success left an ingested export marked `failed` with no receipt. A success finishing after a failure undercounted the attempts. The fix adds each call's attempts with `$inc`, and writes the result only while the status is not `delivered`. A new test runs both finishing orders and fails on the old code.
- **Connect timeouts were described as slow responses.** `httpx.TimeoutException` also covers `ConnectTimeout` and `PoolTimeout`, so a 2-second connect timeout was stored as `no response within 5 s`. That points staff at a slow service when the service is actually unreachable. Connect timeouts and read timeouts now have their own messages, and other transport errors are reported by type. The new `test_retry.py` covers each case, and a check against an unroutable address returned `could not connect within 2 s`.

## Interaction 3: Container deployment, health, and configuration (Q3.1 and Q3.2)

| Field | Description |
|---|---|
| Tool or model | Claude Code (Claude Opus 5.5) |
| Prompt or record | I asked Claude to complete Q3.1 and Q3.2. It was to take service names, request sizes, and timeouts from section 2 and the supplied nginx config, and to list any value it had to assume. I added these requirements: publish a host port for nginx only; seed idempotently; base readiness on a real database query that returns 503 on failure, and point the API healthcheck at it; confirm that each healthcheck's probe tool exists in its image; run the API image as non-root with layers ordered for caching; and keep `.env` out of Git, with safe placeholders in `.env.example`. I also listed five acceptance checks for Claude to run and report. |
| Affected files | `q3_deploy/Dockerfile`, `docker-compose.yml`, `nginx.conf`, `.env.example`, `README.md`, `RUNBOOK.md` (startup section); `q1_api/app/main.py`, `batches.py`, `config.py`, `db.py`; `q1_api/tests/test_startup.py`; `q1_api/README.md`, `q2_integration/README.md`, `REFLECTION.md` |

### Adopted output

Claude generated:
- The API Dockerfile.
- A Compose file with healthchecks for all five services, `service_healthy` startup ordering, a named MongoDB volume, and MongoDB root credentials taken from `.env`.
- The nginx limits and timeouts, with the reasoning behind them.
- `/api/ready`, which pings MongoDB under a 2-second limit.
- A seed import keyed on the file's SHA-256.

It then checked the probe tools in each image and ran the five acceptance checks from a clean copy. The results are in `q3_deploy/README.md`.

## Interaction 4: Backup, runbook, and smoke test (Q3.3)

| Field | Description |
|---|---|
| Tool or model | Claude Code (Claude Opus 5.5) |
| Prompt or record | I asked Claude to continue with Q3.3 before committing Module 3. That meant a backup script that keeps the seven newest files, the remaining runbook sections, and the smoke test. |
| Affected files | `q3_deploy/scripts/backup.sh`, `q3_deploy/scripts/smoke_test.sh`, `q3_deploy/RUNBOOK.md`, `q3_deploy/nginx.conf`, `q3_deploy/docker-compose.yml`, `q3_deploy/README.md`, `.gitignore`, `REFLECTION.md` |

### Adopted output

Claude generated:
- `backup.sh`: an online `mongodump` of the application database to a timestamped, gzip-checked file, keeping the seven newest. Its credentials come from the mongo container's own environment.
- `smoke_test.sh`: drives one batch through nginx from upload to delivery receipt, and fails on any HTTP error.
- The rest of the runbook.

Claude tested each runbook procedure against a running stack: retention over nine backups, a backup with the database stopped, a restore, an OCR outage and its recovery, and log lookups. Testing the restore exposed two nginx problems, and Claude changed:
- A stopped API made clients wait 60 s for a `504`. Claude added `proxy_connect_timeout 2s` for `/api/`.
- nginx looks up upstream addresses only once. Claude added `restart: true` to its `depends_on`, so Compose restarts nginx when it restarts or recreates `api` or `mock_ocr`.
