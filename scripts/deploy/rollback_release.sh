#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_BUNDLE="${1:-}"
OLD_IMAGE_TAG="${OLD_IMAGE_TAG:-}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/docker-compose.server.yml}"
ROLLBACK_HEALTH_URL="${ROLLBACK_HEALTH_URL:-}"
ROLLBACK_STATUS_FILE="${ROLLBACK_STATUS_FILE:-./rollback-result.json}"
ROLLBACK_ALLOW_IN_PLACE="${ROLLBACK_ALLOW_IN_PLACE:-}"
SERVICES=(app worker-outbound worker-background worker-webchat-ai worker-handoff-snapshot)
STATES=()
OUTCOME="fail"
FAILURE_STAGE="INITIALIZATION"
RESTORE_LIST_FILE=""
RESTORE_LIST_RAW_FILE=""

append_state() {
  STATES+=("$1")
}

cleanup_restore_list() {
  if [[ -n "${RESTORE_LIST_FILE:-}" && -f "$RESTORE_LIST_FILE" ]]; then
    rm -f -- "$RESTORE_LIST_FILE"
  fi
  if [[ -n "${RESTORE_LIST_RAW_FILE:-}" && -f "$RESTORE_LIST_RAW_FILE" ]]; then
    rm -f -- "$RESTORE_LIST_RAW_FILE"
  fi
}

write_status() {
  local states_json status_dir
  states_json="$(printf '%s\n' "${STATES[@]}" | python -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
  status_dir="$(dirname "$ROLLBACK_STATUS_FILE")"
  mkdir -p -- "$status_dir"
  STATES_JSON="$states_json" \
  STATUS_FILE="$ROLLBACK_STATUS_FILE" \
  OUTCOME="$OUTCOME" \
  FAILURE_STAGE="$FAILURE_STAGE" \
  python - <<'PY'
import json
import os
from pathlib import Path

states = json.loads(os.environ["STATES_JSON"])
failure_stage = os.environ.get("FAILURE_STAGE") or None
payload = {
    "schema_version": "nexus_operator_rollback_result_v1",
    "outcome": os.environ["OUTCOME"],
    "failure_stage": failure_stage,
    "states": states,
    "database_restore_applied": "DATABASE_RESTORE_APPLIED" in states,
    "database_restored": "DATABASE_RESTORED" in states,
    "image_restarted": "IMAGE_RESTARTED" in states,
    "health_verified": "HEALTH_VERIFIED" in states,
}
Path(os.environ["STATUS_FILE"]).write_text(
    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
}

on_exit() {
  local code=$?
  trap - EXIT
  cleanup_restore_list || true
  if [[ "$code" -ne 0 ]]; then
    OUTCOME="fail"
    write_status || true
  fi
  exit "$code"
}
trap on_exit EXIT

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
  POSTGRES_URL_CANDIDATE="$value" python - <<'PYURL'
import os
from urllib.parse import urlsplit

value = os.environ["POSTGRES_URL_CANDIDATE"]
parsed = urlsplit(value)
if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
    raise SystemExit("postgres_native_url_invalid")
if not parsed.username:
    raise SystemExit("postgres_native_url_user_required")
if parsed.query:
    raise SystemExit("postgres_native_url_query_not_allowed")
if parsed.fragment:
    raise SystemExit("postgres_native_url_fragment_not_allowed")
try:
    parsed.port
except ValueError as exc:
    raise SystemExit("postgres_native_url_port_invalid") from exc
database = parsed.path.lstrip("/")
if not database or "/" in database:
    raise SystemExit("postgres_native_url_database_invalid")
print(value, end="")
PYURL
}

require_health_2xx() {
  local url="$1" http_code
  http_code="$(curl --silent --show-error --max-time 15 --output /dev/null --write-out '%{http_code}' "$url")"
  if [[ ! "$http_code" =~ ^2[0-9][0-9]$ ]]; then
    echo "Health endpoint returned non-2xx status: $http_code" >&2
    return 1
  fi
}

if [[ -z "$BACKUP_BUNDLE" && -z "$OLD_IMAGE_TAG" ]]; then
  append_state "INSTRUCTIONS_ONLY"
  echo "No backup bundle or OLD_IMAGE_TAG supplied; no mutation performed."
fi

if [[ -n "$BACKUP_BUNDLE" ]]; then
  FAILURE_STAGE="DATABASE_BACKUP_VALIDATION"
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

  MANIFEST_FIELDS="$(MANIFEST="$MANIFEST" python - <<'PY'
import json
import os
import re
from pathlib import Path

payload = json.loads(Path(os.environ["MANIFEST"]).read_text(encoding="utf-8"))
if payload.get("schema_version") != "nexus_postgres_backup_manifest_v1":
    raise SystemExit("backup_manifest_schema_invalid")
if payload.get("format") != "postgres_custom" or payload.get("archive") != "database.dump":
    raise SystemExit("backup_manifest_format_invalid")
digest = payload.get("archive_sha256", "")
source_hash = payload.get("source_database_sha256", "")
head = payload.get("alembic_head", "")
size = payload.get("archive_size_bytes")
extensions = payload.get("preinstalled_extensions")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise SystemExit("backup_manifest_digest_invalid")
if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
    raise SystemExit("backup_manifest_source_invalid")
if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(head)):
    raise SystemExit("backup_manifest_head_invalid")
if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
    raise SystemExit("backup_manifest_size_invalid")
if not isinstance(extensions, list) or len(extensions) != 1 or not isinstance(extensions[0], dict):
    raise SystemExit("backup_manifest_extensions_invalid")
extension = extensions[0]
if extension.get("name") != "vector" or not re.fullmatch(r"[A-Za-z0-9._+-]{1,80}", str(extension.get("version", ""))):
    raise SystemExit("backup_manifest_vector_invalid")
print("\t".join((digest, str(size), source_hash, str(head), str(extension["version"]))))
PY
)"
  IFS=$'\t' read -r EXPECTED_SHA EXPECTED_SIZE SOURCE_DATABASE_SHA256 EXPECTED_HEAD EXPECTED_VECTOR_VERSION <<< "$MANIFEST_FIELDS"
  ACTUAL_SHA="sha256:$(sha256sum "$ARCHIVE" | awk '{print $1}')"
  ACTUAL_SIZE="$(wc -c < "$ARCHIVE" | tr -d ' ')"
  if [[ "$EXPECTED_SHA" != "$ACTUAL_SHA" || "$EXPECTED_SIZE" != "$ACTUAL_SIZE" ]]; then
    echo "Backup checksum or size mismatch" >&2
    exit 5
  fi

  TARGET_DATABASE="$(psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c 'SELECT current_database()')"
  TARGET_DATABASE_SHA256="$(printf '%s' "$TARGET_DATABASE" | sha256sum | awk '{print $1}')"
  if [[ "$TARGET_DATABASE_SHA256" == "$SOURCE_DATABASE_SHA256" && "$ROLLBACK_ALLOW_IN_PLACE" != "I_UNDERSTAND" ]]; then
    echo "Refusing in-place restore without ROLLBACK_ALLOW_IN_PLACE=I_UNDERSTAND" >&2
    exit 6
  fi
  TARGET_VECTOR_VERSION="$(psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
  if [[ "$TARGET_VECTOR_VERSION" != "$EXPECTED_VECTOR_VERSION" ]]; then
    echo "Target vector extension version does not match backup manifest" >&2
    exit 10
  fi

  RESTORE_LIST_FILE="$(mktemp "${TMPDIR:-/tmp}/nexus-restore-list.XXXXXX")"
  RESTORE_LIST_RAW_FILE="$RESTORE_LIST_FILE.raw"
  pg_restore --list "$ARCHIVE" > "$RESTORE_LIST_RAW_FILE"
  RAW_RESTORE_LIST="$RESTORE_LIST_RAW_FILE" FILTERED_RESTORE_LIST="$RESTORE_LIST_FILE" python - <<'PY'
import os
import re
from pathlib import Path

raw_path = Path(os.environ["RAW_RESTORE_LIST"])
filtered_path = Path(os.environ["FILTERED_RESTORE_LIST"])
extension_entry = re.compile(r"^\d+;\s+\d+\s+\d+\s+EXTENSION\s+-\s+vector(?:\s+.*)?$")
comment_entry = re.compile(r"^\d+;\s+\d+\s+\d+\s+COMMENT\s+-\s+EXTENSION\s+vector(?:\s+.*)?$")
vector_toc = re.compile(r"^\d+;.*\bEXTENSION\b.*\bvector\b", re.IGNORECASE)
lines = raw_path.read_text(encoding="utf-8").splitlines()
filtered: list[str] = []
extension_count = 0
comment_count = 0
for line in lines:
    if extension_entry.fullmatch(line):
        extension_count += 1
        filtered.append(";" + line)
    elif comment_entry.fullmatch(line):
        comment_count += 1
        filtered.append(";" + line)
    elif vector_toc.search(line):
        raise SystemExit("backup_restore_vector_toc_unrecognized")
    else:
        filtered.append(line)
if extension_count != 1 or comment_count > 1:
    raise SystemExit("backup_restore_vector_toc_invalid")
filtered_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
  rm -f -- "$RESTORE_LIST_RAW_FILE"
  RESTORE_LIST_RAW_FILE=""

  FAILURE_STAGE="DATABASE_RESTORE"
  pg_restore \
    --dbname="$POSTGRES_NATIVE_URL" \
    --exit-on-error \
    --single-transaction \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --use-list="$RESTORE_LIST_FILE" \
    "$ARCHIVE"
  append_state "DATABASE_RESTORE_APPLIED"
  FAILURE_STAGE="DATABASE_POST_VERIFY"
  RESTORED_HEADS="$(psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version ORDER BY version_num')"
  mapfile -t HEAD_ROWS <<< "$RESTORED_HEADS"
  if [[ "${#HEAD_ROWS[@]}" -ne 1 || "${HEAD_ROWS[0]}" != "$EXPECTED_HEAD" ]]; then
    echo "Restored Alembic head does not match backup manifest" >&2
    exit 7
  fi
  RESTORED_VECTOR_VERSION="$(psql "$POSTGRES_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
  if [[ "$RESTORED_VECTOR_VERSION" != "$EXPECTED_VECTOR_VERSION" ]]; then
    echo "Restored vector extension version does not match backup manifest" >&2
    exit 11
  fi
  append_state "DATABASE_RESTORED"
fi

if [[ -n "$OLD_IMAGE_TAG" ]]; then
  if [[ -z "$ROLLBACK_HEALTH_URL" ]]; then
    echo "ROLLBACK_HEALTH_URL is required when OLD_IMAGE_TAG is set" >&2
    exit 8
  fi
  FAILURE_STAGE="IMAGE_RESTART"
  IMAGE_TAG="$OLD_IMAGE_TAG" docker compose -f "$COMPOSE_FILE" up -d "${SERVICES[@]}"
  append_state "IMAGE_RESTARTED"
  FAILURE_STAGE="HEALTH_VERIFICATION"
  require_health_2xx "$ROLLBACK_HEALTH_URL/healthz"
  require_health_2xx "$ROLLBACK_HEALTH_URL/readyz"
  append_state "HEALTH_VERIFIED"
fi

OUTCOME="pass"
FAILURE_STAGE=""
cleanup_restore_list
write_status
trap - EXIT
printf 'rollback_states=%s\n' "$(IFS=,; echo "${STATES[*]}")"
