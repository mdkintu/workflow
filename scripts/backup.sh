#!/usr/bin/env bash
# Nightly backup (docs/02-architecture.md §10, ADR-13): pg_dump + a tar of
# the media and Caddy-data volumes, with checksums, into
# $BACKUP_DIR/<YYYYMMDD-HHMM>/. Run via `make backup` or a host cron/timer.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

BACKUP_DIR="${BACKUP_DIR:-./backups}"
POSTGRES_USER="${POSTGRES_USER:-workflow}"
POSTGRES_DB="${POSTGRES_DB:-workflow}"

project_name() {
  docker compose config --format json 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("name", "workflow"))' 2>/dev/null \
    || echo "workflow"
}

PROJECT_NAME="$(project_name)"
STAMP="$(date +%Y%m%d-%H%M)"
DEST="$BACKUP_DIR/$STAMP"
mkdir -p "$DEST"
DEST_ABS="$(cd "$DEST" && pwd)"

echo "==> Dumping database to $DEST/db.dump"
docker compose exec -T db pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" > "$DEST/db.dump"

echo "==> Archiving media volume (${PROJECT_NAME}_media)"
docker run --rm \
  -v "${PROJECT_NAME}_media:/data:ro" \
  -v "$DEST_ABS:/backup" \
  alpine:3 \
  sh -c "tar -czf /backup/media.tar.gz -C /data ."

echo "==> Archiving Caddy data volume (${PROJECT_NAME}_caddy_data)"
docker run --rm \
  -v "${PROJECT_NAME}_caddy_data:/data:ro" \
  -v "$DEST_ABS:/backup" \
  alpine:3 \
  sh -c "tar -czf /backup/caddy_data.tar.gz -C /data ."

echo "==> Writing checksums"
(cd "$DEST" && sha256sum db.dump media.tar.gz caddy_data.tar.gz > SHA256SUMS)

echo "==> Backup complete: $DEST"
