#!/usr/bin/env bash
set -Eeuo pipefail

: "${ROLLBACK_CONFIRM:?Set ROLLBACK_CONFIRM=I_UNDERSTAND to run rollback steps}"
if [ "$ROLLBACK_CONFIRM" != "I_UNDERSTAND" ]; then
  echo "ROLLBACK_CONFIRM must equal I_UNDERSTAND"
  exit 2
fi

BACKUP_SQL_GZ="${1:-}"
OLD_IMAGE_TAG="${OLD_IMAGE_TAG:-}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.cloud.yml}"

echo "== NexusDesk rollback helper =="
echo "compose_file=$COMPOSE_FILE"
echo "old_image_tag=${OLD_IMAGE_TAG:-[not set]}"
echo "backup_sql_gz=${BACKUP_SQL_GZ:-[not set]}"

if [ -n "$OLD_IMAGE_TAG" ]; then
  echo "Set your compose image tag back to: $OLD_IMAGE_TAG"
  echo "Then run: docker compose -f $COMPOSE_FILE up -d app worker sync-daemon event-daemon"
fi

if [ -n "$BACKUP_SQL_GZ" ]; then
  if [ ! -f "$BACKUP_SQL_GZ" ]; then
    echo "Backup file not found: $BACKUP_SQL_GZ"
    exit 3
  fi
  : "${DATABASE_URL:?DATABASE_URL is required to restore database backup}"
  echo "Restoring database from $BACKUP_SQL_GZ"
  gunzip -c "$BACKUP_SQL_GZ" | psql "$DATABASE_URL"
fi

echo "Rollback helper completed. Validate /healthz, /readyz, login, and ticket list manually."
