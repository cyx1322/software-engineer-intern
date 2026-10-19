# Operations runbook

Run commands from `q3_deploy/`. Only nginx is published, at `http://localhost:8080`. Every step was tested on 2026-10-19 with Docker 27.4 and Compose 2.31.

## Start the system

1. Run `cp .env.example .env`. For anything but a local stack, change `MONGO_PASSWORD`, using letters and digits only. The other values already match the Compose service names. MongoDB creates the account only when its volume is first initialized. To change the credentials later, run `docker compose down -v`, which deletes the data.
2. Run `docker compose up --build`. Add `-d --wait` to return once every service is healthy. The system is up when `docker compose ps` shows all five services as `(healthy)`. The API imports the sample CSV once as a seed batch; restarts skip it.
3. Run `curl -s localhost:8080/api/ready`. Expected result: `{"status":"ready"}`. A `503` with `database unreachable` means the API cannot reach MongoDB. Check `docker compose ps mongo` and `docker compose logs mongo`.
4. Run `bash scripts/smoke_test.sh` in another terminal. Expected result: seven numbered steps, then `PASS`. Step 6 prints the account-opening receipt, for example `receipt DLV-0001`. It takes about 30 seconds, most of it OCR.

## Read logs

Run `docker compose logs --tail=100 api mock_ocr mock_onboarding nginx` to see recent logs. Add `-f` to follow, and use `docker compose logs mongo` for the database. The API log names what staff search for:

- **A case**, by `case_ref`. Run `docker compose logs api | grep APPL-2026-0010` to see lines such as `OCR APPL-2026-0010: attempt 1/6 failed (no response within 5 s); retrying in 0.4 s` and `case APPL-2026-0009: ocr_failed: cannot read identity-card fields ...`.
- **A batch**, by its id: `batch <id>: queued 43 cases for OCR`, then later `batch <id>: OCR processing finished`.
- **An export**, by filename: `delivery EXPORT-<id>.csv: attempt 1/6 failed (HTTP 500: ingestion temporarily unavailable)`.

nginx writes one access-log line per request, with the path and status. A `504` on `/api/` means the API container is down. Logs belong to the current container: recreating it, for example with `up --build`, discards the earlier lines.

## Diagnose OCR failures

1. Check OCR from the API's side of the network:

   ```bash
   docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://mock_ocr:9100/online', timeout=5).read().decode())"
   ```

   Expected response: `{"online": true}`. `docker compose ps mock_ocr` should show `(healthy)`.
2. Inspect `docker compose logs --tail=100 mock_ocr api`, then list the failed cases with `curl -s "localhost:8080/api/batches/<batch_id>/cases?status=ocr_failed"`. Each failed case has an `ocr_error` with a `reason` and a `retryable` flag. The reason tells the failures apart:
   - `no response within 5 s`: the service is slow or hanging.
   - `Name or service not known` (the container is stopped), `Connection refused`, or `could not connect within 2 s`: the service is unreachable.
   - The OCR service's own message, with `retryable: false`: the scan is unreadable.
3. Recover as follows:
   - **OCR service unavailable.** Run `docker compose up -d --wait mock_ocr`. Then run `curl -s -X POST localhost:8080/api/batches/<batch_id>/process`.
   - **Timeouts or exhausted retries** (`retryable: true`). Run the same `process` call. It moves these cases back to `pending` and retries them. It also picks up cases left `pending` by an API restart. Finished cases are not sent again.
   - **Unreadable scan** (`retryable: false`). This needs a corrected image before processing again. Upload the corrected rows as a new batch.

## Back up and restore

Run `bash scripts/backup.sh` while the system is running. It writes `backups/idv-<UTC time>.archive.gz`, a gzipped `mongodump` archive of the application database, and keeps only the 7 newest. The `backups/` directory holds applicant data and is git-ignored.

1. Pick a backup with `ls -1 backups/`. Run `docker compose stop api` to stop writes. Meanwhile nginx answers `/api/` with `504`.
2. Restore the backup. `--drop` replaces each collection with its contents in the backup:

   ```bash
   docker compose exec -T mongo sh -c 'mongorestore --quiet --archive --gzip --drop -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' < backups/idv-20261019T054925Z.archive.gz
   ```

3. Run `docker compose start api`, then `curl -s localhost:8080/api/ready`. Expected result: `{"status":"ready"}`. `GET /api/batches/<batch_id>` shows the batch as it was at the backup time, and a batch created after the backup returns `404`. `docker compose ps` may show nginx as `(unhealthy)` for up to 10 seconds, because its healthcheck goes through the API.
