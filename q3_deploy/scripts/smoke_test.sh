#!/usr/bin/env bash
# Drives one batch end-to-end through nginx:
# upload -> process -> review -> export -> deliver, printing each step.
# `bash scripts/smoke_test.sh` must pass right after a clean
# `docker compose up --build`. Needs curl and python3 on the host.
set -euo pipefail
BASE=${BASE:-http://localhost:8080}
CSV=${CSV:-"$(dirname "$0")/../../data/account_applications.csv"}
OCR_WAIT_SECONDS=${OCR_WAIT_SECONDS:-120}

fail() {
    echo "FAIL: $*" >&2
    exit 1
}
# call METHOD PATH [curl args...]: print the response body, or fail on an HTTP error status.
call() {
    local method=$1 path=$2
    shift 2
    curl -sS --fail-with-body -X "$method" "$BASE/api$path" "$@" || fail "$method $path"
}
# field EXPR: evaluate a Python expression over the JSON on stdin, available as d.
field() { python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"; }
counts='{k: v for k, v in d["status_counts"].items() if v}'

ready=$(call GET /ready)
echo "1. ready:    $ready"

batch=$(call POST /batches -F "file=@$CSV")
id=$(field 'd["id"]' <<<"$batch")
echo "2. upload:   batch $id, $(field '"%(accepted_count)s accepted, %(rejected_count)s rejected" % d' <<<"$batch")"

started=$(call POST "/batches/$id/process")
echo "3. process:  queued $(field 'd["queued"]' <<<"$started") cases for OCR"
for ((waited = 0; ; waited++)); do
    summary=$(call GET "/batches/$id")
    [ "$(field 'd["processing"]' <<<"$summary")" = False ] && break
    [ "$waited" -lt "$OCR_WAIT_SECONDS" ] || fail "OCR still running after ${OCR_WAIT_SECONDS}s"
    sleep 1
done
echo "   OCR done after ~${waited}s: $(field "$counts" <<<"$summary")"

done_cases=$(call GET "/batches/$id/cases?status=ocr_done&page_size=1")
case_id=$(field 'd["items"][0]["id"]' <<<"$done_cases") || fail "no ocr_done case to review"
case_ref=$(field 'd["items"][0]["case_ref"]' <<<"$done_cases")
for stage in 1 2; do
    reviewed=$(call POST "/cases/$case_id/review" -H 'Content-Type: application/json' \
        -d "{\"stage\": $stage, \"decision\": \"approve\"}")
    echo "4. review:   $case_ref stage $stage approve -> $(field 'd["status"]' <<<"$reviewed")"
done

exported=$(call POST "/batches/$id/export")
echo "5. export:   $(field '"%s with %s" % (d["filename"], d["case_refs"])' <<<"$exported")"
delivery=$(field 'd["delivery"]["status"]' <<<"$exported")
[ "$delivery" = delivered ] || fail "delivery $delivery: $(field 'd["delivery"]["error"]' <<<"$exported")"
echo "6. deliver:  $(field '"receipt %(delivery_id)s, sha256 %(sha256).12s…" % d["delivery"]["receipt"]' <<<"$exported"), $(field 'd["delivery"]["attempts"]' <<<"$exported") attempt(s)"

final=$(call GET "/batches/$id")
echo "7. batch:    $(field "$counts" <<<"$final")"
echo "PASS"
