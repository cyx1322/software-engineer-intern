# Module 3: Deployment and operations

Complete the supplied deployment stubs to meet the [Module 3 requirements](../README.md#5-module-3-deployment-and-operations-30-points).

| File | Required content |
|---|---|
| `Dockerfile` | API image, dependencies, application files, and startup command |
| `docker-compose.yml` | Services, persistent storage, health checks, and startup dependencies |
| `nginx.conf` | API and OCR routing, request-body limit, and proxy timeouts |
| `.env.example` | Environment variable names and non-secret example values |
| `scripts/backup.sh` | Database backup and retention of the seven most recent files |
| `scripts/smoke_test.sh` | One batch through import, OCR, both reviews, export, and delivery |
| `RUNBOOK.md` | Tested startup, log access, OCR diagnosis, and restore instructions |

The Compose build context for the API is the repository root. Dockerfile `COPY` paths must use that context. Keep filenames and service names as specified in the [repository layout](../README.md#2-fixed-structure-and-naming).

## Configure and start

Run these commands from `q3_deploy/` after completing the deployment files:

```bash
cp .env.example .env
docker compose up --build
```

Set the database and service addresses in `.env` for the implemented configuration. Keep `.env` out of Git. Record the configuration steps and startup checks in [RUNBOOK.md](RUNBOOK.md).

After startup, run the smoke test in another terminal from `q3_deploy/`:

```bash
bash scripts/smoke_test.sh
```

## Configure nginx

Preserve `/ocr/parse` when forwarding OCR requests. A `proxy_pass` URI can change the forwarded path. Verify the received path against the [OCR endpoint](../mock_ocr/README.md#request-contract).

Set `client_max_body_size` for the expected upload sizes and test the limit. Set `proxy_read_timeout` to match the intended client-visible behavior during an upstream delay. Be prepared to explain the chosen values during the interview.

Verify that `/api/*` reaches the API and `/ocr/*` reaches the mock OCR service through nginx.

## Implementation

### Services and startup order

| Service | Image | Host port | Healthcheck (probe tool in the image) | Waits for |
|---|---|---|---|---|
| `mongo` | `mongo:8` | none | `mongosh --eval "db.adminCommand('ping')"` | nothing |
| `api` | built from `Dockerfile` | none | Python `urllib` GET `/api/ready`. This is readiness, not liveness; the image has no `curl` or `wget`. | `mongo` healthy |
| `mock_ocr` | built from `../mock_ocr` | none | Python `urllib` GET `/online` | nothing |
| `mock_onboarding` | built from `../mock_onboarding` | none | Python `urllib` GET `/online` | nothing |
| `nginx` | `nginx:1.27-alpine` | `8080` → `80` | busybox `wget` GET `/api/health` through its own proxy | `api` and `mock_ocr` healthy. With `restart: true`, Compose restarts nginx whenever it restarts or recreates either one. |

There are no fixed sleeps. nginx looks up both upstream addresses once, when it starts. It therefore waits for both services it proxies to, and Compose restarts it when either one is restarted or recreated. MongoDB data lives in the named volume `mongo_data`, which `docker compose down` keeps and `docker compose down -v` deletes.

- **`/api/health`** is liveness. It returns `200` whenever the process is serving and never touches the database.
- **`/api/ready`** is readiness. It runs MongoDB's `ping` command, the equivalent of `SELECT 1`, with a 2-second limit. It returns `200 {"status": "ready"}`, or `503 {"status": "not ready", "reason": "database unreachable"}` and logs the error.
- **Seeding.** With `SEED_CSV` set, the API imports that CSV as a batch when it starts. The batch stores the file's SHA-256 in `seed_sha256` under a unique index, so later starts log `already imported` and add nothing. The seed batch is imported only; OCR runs when someone calls `process`.
- **Image.** The API image installs `requirements.txt` before copying the code, so a code change does not reinstall dependencies. It runs as the non-root user `app` (uid 10001).

### nginx values

| Setting | Value | Reason |
|---|---|---|
| `proxy_pass` | `http://api:8000` and `http://mock_ocr:9100`, with no URI part | The path is forwarded unchanged, so `/ocr/parse` arrives as `/ocr/parse`. |
| `client_max_body_size` (`/api/`) | `10m` | The supplied config says batch CSVs exceed the 1 MB default. 10 MB holds about 110,000 sample-format rows. `/ocr/` keeps the 1 MB default, because its JSON bodies are tiny. |
| `proxy_read_timeout` (`/api/`) | `60s` | The slowest API request is an export, which delivers before it responds. Its worst case is 6 attempts × 5 s plus 15.5 s of backoff, about 46 s. |
| `proxy_connect_timeout` (`/api/`) | `2s` | Connections to a stopped `api` container hang instead of being refused. Under the 60 s default, clients waited 60 s for a `504` during the restore test; with `2s` they get it in 2 s. Containers on the same network connect in well under a millisecond. |
| `proxy_read_timeout`, `proxy_connect_timeout` (`/ocr/`) | `5s`, `2s` | The same limits as the API's OCR client. A hung parse returns `504` after 5 s, instead of holding the client for the mock's 600 s delay. |

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on no other event. Each push runs two jobs:

| Job | Runs when | Steps |
|---|---|---|
| `test` | every push to `main` | On Python 3.10 and 3.12: `pip install -r requirements.txt`, then `python -m pytest q1_api/` twice. The first run uses mongomock. The second sets `TEST_MONGO_URL` to a `mongo:8` service container. |
| `build` | only after every `test` job succeeds (`needs: test`) | `cp .env.example .env`, then `docker compose build` in `q3_deploy/`. It logs in to GHCR with `GITHUB_TOKEN` and pushes `ghcr.io/<owner>/<repo>/api` tagged with the commit SHA and `latest`. |

A package published with `GITHUB_TOKEN` inherits the repository's visibility, so a public fork publishes a public image.

Checked locally before the first push: actionlint reported no problems, and `act --list` showed `test` then `build`. The four test runs (two Python versions, each on mongomock and MongoDB 8) all passed. `docker compose build` from a clean checkout produced `q3_deploy-api`, and the lowercase image name resolved as expected in bash 5.

## Verification

These checks were run on 2026-10-19, from a copy of the repository containing only tracked files, after `cp .env.example .env`. The engine was Docker 27.4 with Compose 2.31.

| Check | Result |
|---|---|
| `docker compose up --build` | All five services reported healthy after 14 s. They started in order: `mongo` and the mocks first, `api` 2.6 s later, then `nginx`. The API log showed `seed ... imported as batch ... (43 cases)`. |
| Routes through nginx on port 8080 | `/api/health` returned `200 {"status":"ok"}`, and `/api/ready` returned `200 {"status":"ready"}`. `POST /ocr/parse` returned the mock's own outcomes: `200`, `500`, `422` with its reason, and `504` after 5.0 s for a hang. The mock recorded all 12 requests at `/ocr/parse`. |
| Upload size limit | A multipart body of exactly 10,485,760 bytes (112,748 rows) returned `201` in 1.3 s. A body of 10,485,761 bytes returned nginx's `413`. |
| `docker compose down`, then `up` | The same seed batch id and its 43 cases were still there, with exactly one seed batch. The API log showed `already imported`. A processed batch kept its 38 `ocr_done` and 5 `ocr_failed` cases. |
| Database unreachable (`docker compose stop mongo`) | `/api/ready` returned `503` in about 2.2 s, and `/api/health` returned `200`. Docker marked `api` unhealthy while `nginx` stayed healthy. After `docker compose start mongo`, `api` was healthy again within 10 s. |
| Wiring and caching | An export through nginx was delivered with a receipt. After a code-only change, a rebuild reused every layer up to and including `pip install`. |
| `bash scripts/smoke_test.sh` right after `up --build` | Steps 1–7 printed, then `PASS`, in about 30 s. OCR finished in 18–23 s with 38 `ocr_done` and 5 `ocr_failed`. One case was approved at both stages, exported, and delivered (`receipt DLV-0001`). With nginx unreachable, the script printed `FAIL: GET /ready` and exited 1. |
| `bash scripts/backup.sh` | Nine runs left the seven newest files; the listings are in [REFLECTION.md](../REFLECTION.md#backup-retention). With `mongo` stopped, the script exited 1 and kept nothing. The RUNBOOK restore brought the data back to the backup state. |
| API stopped | nginx returned `504` in 2.0 s. After `docker compose start api`, `/api/ready` returned `200`. `docker compose restart api` also restarted nginx. |
| OCR stopped during a batch | All 43 cases ended `ocr_failed` with `retryable: true` and `Name or service not known` after 6 attempts each. After `docker compose up -d --wait mock_ocr` and a second `process`, the batch recovered to 38 `ocr_done` and 5 `ocr_failed` in 21 s. |
| Final full rerun | After the nginx and Compose changes, every check above was repeated on a new clean copy, with the same results. |
