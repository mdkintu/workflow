#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (docs/02-architecture.md §10).
# Usage: scripts/restore.sh YYYYMMDD-HHMM
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

STAMP="${1:?Usage: scripts/restore.sh YYYYMMDD-HHMM}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
SRC="$BACKUP_DIR/$STAMP"
POSTGRES_USER="${POSTGRES_USER:-workflow}"
POSTGRES_DB="${POSTGRES_DB:-workflow}"

if [ ! -d "$SRC" ]; then
  echo "No such backup: $SRC" >&2
  exit 1
fi

project_name() {
  docker compose config --format json 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("name", "workflow"))' 2>/dev/null \
    || echo "workflow"
}

echo "==> Verifying checksums"
(cd "$SRC" && sha256sum -c SHA256SUMS)

PROJECT_NAME="$(project_name)"
SRC_ABS="$(cd "$SRC" && pwd)"

echo "==> Stopping web, worker and beat"
docker compose stop web worker beat

echo "==> Restoring database"
docker compose exec -T db pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$SRC/db.dump"

echo "==> Restoring media volume (${PROJECT_NAME}_media)"
docker run --rm \
  -v "${PROJECT_NAME}_media:/data" \
  -v "$SRC_ABS:/backup:ro" \
  alpine:3 \
  sh -c "find /data -mindepth 1 -delete; tar -xzf /backup/media.tar.gz -C /data"

echo "==> Restoring Caddy data volume (${PROJECT_NAME}_caddy_data)"
docker run --rm \
  -v "${PROJECT_NAME}_caddy_data:/data" \
  -v "$SRC_ABS:/backup:ro" \
  alpine:3 \
  sh -c "find /data -mindepth 1 -delete; tar -xzf /backup/caddy_data.tar.gz -C /data"

echo "==> Running migrate (in case this is a newer app version)"
docker compose run --rm web python manage.py migrate

echo "==> Starting services"
docker compose start web worker beat

echo "==> Restore complete from $SRC"
