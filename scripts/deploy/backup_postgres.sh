#!/usr/bin/env bash
set -Eeuo pipefail

normalize_native_url() {
  local value="${POSTGRES_NATIVE_URL:-${DATABASE_URL:-}}"
  case "$value" in
    postgresql+psycopg://*) value="postgresql://${value#postgresql+psycopg://}" ;;
    postgresql+psycopg2://*) value="postgresql://${value#postgresql+psycopg2://}" ;;
    postgres+psycopg://*) value="postgresql://${value#postgres+psycopg://}" ;;
  esac
  case "$value" in
    postgresql://*|postgres://*) printf '%s' "$value" ;;
    *) echo "POSTGRES_NATIVE_URL must be a libpq postgresql:// URI" >&2; return 2 ;;
  esac
}

POSTGRES_NATIVE_URL="$(normalize_native_url)"
OUT_DIR="${1:-./backups}"
umask 077
mkdir -p -- "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_BUNDLE="$OUT_DIR/helpdesk_${STAMP}"
TMP_BUNDLE="$(mktemp -d "$OUT_DIR/.helpdesk_${STAMP}.XXXXXX")"
ARCHIVE="$TMP_BUNDLE/database.dump"
MANIFEST="$TMP_BUNDLE/backup_manifest.json"

cleanup() {
  if [[ -n "${TMP_BUNDLE:-}" && -d "$TMP_BUNDLE" ]]; then
    rm -rf -- "$TMP_BUNDLE"
  fi
}
trap cleanup EXIT

pg_dump \
  --dbname="$POSTGRES_NATIVE_URL" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-privileges \
  --file="$ARCHIVE"

test -s "$ARCHIVE"
pg_restore --list "$ARCHIVE" >/dev/null
BACKUP_SHA256="sha256:$(sha256sum "$ARCHIVE" | awk '{print $1}')"
BACKUP_SIZE_BYTES="$(wc -c < "$ARCHIVE" | tr -d ' ')"
SOURCE_DATABASE="$(psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c 'SELECT current_database()')"
mapfile -t ALEMBIC_HEADS < <(
  psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 \
    -c 'SELECT version_num FROM alembic_version ORDER BY version_num'
)
if [[ "${#ALEMBIC_HEADS[@]}" -ne 1 || -z "${ALEMBIC_HEADS[0]}" ]]; then
  echo "Expected exactly one Alembic head before backup" >&2
  exit 3
fi

MANIFEST="$MANIFEST" \
BACKUP_SHA256="$BACKUP_SHA256" \
BACKUP_SIZE_BYTES="$BACKUP_SIZE_BYTES" \
SOURCE_DATABASE="$SOURCE_DATABASE" \
ALEMBIC_HEAD="${ALEMBIC_HEADS[0]}" \
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
python - <<'PY'
import hashlib
import json
import os
from pathlib import Path

source_identity = hashlib.sha256(os.environ["SOURCE_DATABASE"].encode("utf-8")).hexdigest()
payload = {
    "schema_version": "nexus_postgres_backup_manifest_v1",
    "format": "postgres_custom",
    "archive": "database.dump",
    "archive_sha256": os.environ["BACKUP_SHA256"],
    "archive_size_bytes": int(os.environ["BACKUP_SIZE_BYTES"]),
    "source_database_sha256": source_identity,
    "alembic_head": os.environ["ALEMBIC_HEAD"],
    "created_at": os.environ["CREATED_AT"],
}
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
Path(os.environ["MANIFEST"]).write_text(encoded, encoding="utf-8")
PY

test -s "$MANIFEST"
test ! -e "$FINAL_BUNDLE"
mv -- "$TMP_BUNDLE" "$FINAL_BUNDLE"
TMP_BUNDLE=""
trap - EXIT

echo "Backup bundle written to $FINAL_BUNDLE"
