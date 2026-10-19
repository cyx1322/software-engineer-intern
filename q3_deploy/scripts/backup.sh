#!/usr/bin/env bash
# Back up the application database while the stack is running, to a
# timestamped file, and keep only the 7 most recent backups.
#   bash scripts/backup.sh                     (works from any directory)
#   BACKUP_DIR=/mnt/backups bash scripts/backup.sh
# Restore steps are in RUNBOOK.md.
set -euo pipefail
cd "$(dirname "$0")/.." # q3_deploy/, where docker-compose.yml and .env live

KEEP=7
BACKUP_DIR=${BACKUP_DIR:-backups}
DB=$(sed -n 's/^MONGO_DB=//p' .env | tail -n 1)
DB=${DB:-identity_verification}

mkdir -p "$BACKUP_DIR"
target="$BACKUP_DIR/idv-$(date -u +%Y%m%dT%H%M%SZ).archive.gz"
if [ -e "$target" ]; then
    echo "backup.sh: $target already exists; run again in a second" >&2
    exit 1
fi
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

# mongodump reads from the running server. The credentials come from the
# mongo container's own environment, so they never pass through this script.
docker compose exec -T mongo sh -c 'mongodump --quiet --archive --gzip --db "$1" \
    -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' \
    sh "$DB" >"$partial"
gzip -t "$partial" # a truncated dump fails here and is never kept or counted
mv "$partial" "$target"
echo "backup written: $target ($(wc -c <"$target" | tr -d ' ') bytes)"

# File names sort by time, so everything after the newest $KEEP is older.
ls -1 "$BACKUP_DIR"/idv-*.archive.gz | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -- "$old"
    echo "removed old backup: $old"
done
