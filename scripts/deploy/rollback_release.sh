#!/usr/bin/env bash
set -Eeuo pipefail

: "${ROLLBACK_CONFIRM:?Set ROLLBACK_CONFIRM=I_UNDERSTAND to run rollback steps}"
if [[ "$ROLLBACK_CONFIRM" != "I_UNDERSTAND" ]]; then
  echo "ROLLBACK_CONFIRM must equal I_UNDERSTAND" >&2
  exit 2
fi

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

BACKUP_BUNDLE="${1:-}"
OLD_IMAGE_TAG="${OLD_IMAGE_TAG:-}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.server.yml}"
ROLLBACK_HEALTH_URL="${ROLLBACK_HEALTH_URL:-}"
ROLLBACK_STATUS_FILE="${ROLLBACK_STATUS_FILE:-./rollback-result.json}"
SERVICES=(app worker-outbound worker-background worker-webchat-ai worker-handoff-snapshot)
STATES=()

append_state() {
  STATES+=("$1")
}

if [[ -z "$BACKUP_BUNDLE" && -z "$OLD_IMAGE_TAG" ]]; then
  append_state "INSTRUCTIONS_ONLY"
  echo "No backup bundle or OLD_IMAGE_TAG supplied; no mutation performed."
fi

if [[ -n "$BACKUP_BUNDLE" ]]; then
  POSTGRES_NATIVE_URL="$(normalize_native_url)"
  if [[ ! -d "$BACKUP_BUNDLE" ]]; then
    echo "Backup bundle not found: $BACKUP_BUNDLE" >&2
    exit 3
  fi
  ARCHIVE="$BACKUP_BUNDLE/database.dump"
  MANIFEST="$BACKUP_BUNDLE/backup_manifest.json"
  if [[ ! -f "$ARCHIVE" || -L "$ARCHIVE" || ! -f "$MANIFEST" || -L "$MANIFEST" ]]; then
    echo "Backup bundle must contain regular database.dump and backup_manifest.json files" >&2
    exit 4
  fi
  EXPECTED_SHA="$(MANIFEST="$MANIFEST" python - <<'PY'
import json
import os
import re
from pathlib import Path
payload = json.loads(Path(os.environ["MANIFEST"]).read_text(encoding="utf-8"))
if payload.get("schema_version") != "nexus_postgres_backup_manifest_v1":
    raise SystemExit("backup_manifest_schema_invalid")
value = payload.get("archive_sha256", "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
    raise SystemExit("backup_manifest_digest_invalid")
print(value)
PY
)"
  ACTUAL_SHA="sha256:$(sha256sum "$ARCHIVE" | awk '{print $1}')"
  if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]]; then
    echo "Backup checksum mismatch" >&2
    exit 5
  fi
  pg_restore --list "$ARCHIVE" >/dev/null
  pg_restore \
    --dbname="$POSTGRES_NATIVE_URL" \
    --exit-on-error \
    --single-transaction \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    "$ARCHIVE"
  psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 \
    -c 'SELECT version_num FROM alembic_version ORDER BY version_num' >/dev/null
  append_state "DATABASE_RESTORED"
fi

if [[ -n "$OLD_IMAGE_TAG" ]]; then
  if [[ -z "$ROLLBACK_HEALTH_URL" ]]; then
    echo "ROLLBACK_HEALTH_URL is required when OLD_IMAGE_TAG is set" >&2
    exit 6
  fi
  IMAGE_TAG="$OLD_IMAGE_TAG" docker compose -f "$COMPOSE_FILE" up -d "${SERVICES[@]}"
  append_state "IMAGE_RESTARTED"
  curl --fail --silent --show-error --max-time 15 "$ROLLBACK_HEALTH_URL/healthz" >/dev/null
  curl --fail --silent --show-error --max-time 15 "$ROLLBACK_HEALTH_URL/readyz" >/dev/null
  append_state "HEALTH_VERIFIED"
fi

STATES_JSON="$(printf '%s\n' "${STATES[@]}" | python -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
STATUS_DIR="$(dirname "$ROLLBACK_STATUS_FILE")"
mkdir -p -- "$STATUS_DIR"
STATES_JSON="$STATES_JSON" STATUS_FILE="$ROLLBACK_STATUS_FILE" python - <<'PY'
import json
import os
from pathlib import Path
states = json.loads(os.environ["STATES_JSON"])
payload = {
    "schema_version": "nexus_operator_rollback_result_v1",
    "states": states,
    "database_restored": "DATABASE_RESTORED" in states,
    "image_restarted": "IMAGE_RESTARTED" in states,
    "health_verified": "HEALTH_VERIFIED" in states,
}
Path(os.environ["STATUS_FILE"]).write_text(
    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

printf 'rollback_states=%s\n' "$(IFS=,; echo "${STATES[*]}")"
