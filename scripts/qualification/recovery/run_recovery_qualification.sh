#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_APP_URL:?SOURCE_APP_URL is required}"
: "${SOURCE_NATIVE_URL:?SOURCE_NATIVE_URL is required}"
: "${RESTORE_APP_URL:?RESTORE_APP_URL is required}"
: "${RESTORE_NATIVE_URL:?RESTORE_NATIVE_URL is required}"
: "${RECOVERY_ADMIN_NATIVE_URL:?RECOVERY_ADMIN_NATIVE_URL is required}"
: "${RECOVERY_ALLOW_DATABASE_RECREATE:?RECOVERY_ALLOW_DATABASE_RECREATE is required}"
: "${SOURCE_SHA:?SOURCE_SHA is required}"
: "${QUALIFICATION_MARKER:=QUAL-RCV-001}"
: "${RTO_TARGET_SECONDS:=120}"
: "${RPO_TARGET_SECONDS:=60}"

if [[ "$RECOVERY_ALLOW_DATABASE_RECREATE" != "I_UNDERSTAND" ]]; then
  echo "RECOVERY_ALLOW_DATABASE_RECREATE must equal I_UNDERSTAND" >&2
  exit 2
fi

SOURCE_APP_URL="$SOURCE_APP_URL" \
SOURCE_NATIVE_URL="$SOURCE_NATIVE_URL" \
RESTORE_APP_URL="$RESTORE_APP_URL" \
RESTORE_NATIVE_URL="$RESTORE_NATIVE_URL" \
RECOVERY_ADMIN_NATIVE_URL="$RECOVERY_ADMIN_NATIVE_URL" \
python - <<'PY'
import os
from urllib.parse import urlsplit


def parse(
    name: str,
    *,
    expected_db: str | None = None,
    expected_user: str,
    native: bool = False,
):
    value = os.environ[name]
    parsed = urlsplit(value)
    allowed = {"postgresql", "postgres"} if native else {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
    if parsed.scheme not in allowed or not parsed.hostname:
        raise SystemExit(f"recovery_url_invalid:{name}")
    if not parsed.username:
        raise SystemExit(f"recovery_url_user_required:{name}")
    if parsed.username != expected_user:
        raise SystemExit(f"recovery_user_name_mismatch:{name}")
    if parsed.query:
        raise SystemExit(f"recovery_url_query_not_allowed:{name}")
    if parsed.fragment:
        raise SystemExit(f"recovery_url_fragment_not_allowed:{name}")
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise SystemExit(f"recovery_url_port_invalid:{name}") from exc
    database = parsed.path.lstrip("/")
    if not database or "/" in database:
        raise SystemExit(f"recovery_database_invalid:{name}")
    if expected_db is not None and database != expected_db:
        raise SystemExit(f"recovery_database_name_mismatch:{name}")
    return parsed.hostname.lower(), port, database, parsed.username


source_app = parse(
    "SOURCE_APP_URL",
    expected_db="nexus_source",
    expected_user="nexus_recovery_source",
)
source_native = parse(
    "SOURCE_NATIVE_URL",
    expected_db="nexus_source",
    expected_user="nexus_recovery_source",
    native=True,
)
restore_app = parse(
    "RESTORE_APP_URL",
    expected_db="nexus_restore",
    expected_user="nexus_recovery_restore",
)
restore_native = parse(
    "RESTORE_NATIVE_URL",
    expected_db="nexus_restore",
    expected_user="nexus_recovery_restore",
    native=True,
)
admin = parse(
    "RECOVERY_ADMIN_NATIVE_URL",
    expected_user="nexus_recovery_admin",
    native=True,
)

if source_app != source_native or restore_app != restore_native:
    raise SystemExit("recovery_application_native_identity_mismatch")
if source_native[:2] != restore_native[:2] or source_native[:2] != admin[:2]:
    raise SystemExit("recovery_admin_cluster_mismatch")
if admin[2] in {source_native[2], restore_native[2]}:
    raise SystemExit("recovery_admin_database_not_isolated")
if len({source_native[3], restore_native[3], admin[3]}) != 3:
    raise SystemExit("recovery_role_identity_collision")
PY

ROLE_PROOF="$(psql "$RECOVERY_ADMIN_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 --field-separator='|' -c "
SELECT
  count(*) FILTER (
    WHERE rolname = 'nexus_recovery_source'
      AND rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
      AND NOT rolreplication AND NOT rolbypassrls
  ),
  count(*) FILTER (
    WHERE rolname = 'nexus_recovery_restore'
      AND rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
      AND NOT rolreplication AND NOT rolbypassrls
  ),
  count(*) FILTER (
    WHERE rolname = 'nexus_recovery_admin'
      AND rolcanlogin AND rolsuper AND rolcreatedb
  )
FROM pg_roles
WHERE rolname IN ('nexus_recovery_source', 'nexus_recovery_restore', 'nexus_recovery_admin');
")"
if [[ "$ROLE_PROOF" != "1|1|1" ]]; then
  echo "recovery_role_privilege_proof_failed" >&2
  exit 3
fi

EVIDENCE_ROOT="${EVIDENCE_ROOT:-artifacts/recovery}"
mkdir -p -- "$EVIDENCE_ROOT"

psql "$RECOVERY_ADMIN_NATIVE_URL" -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE IF EXISTS nexus_source;
DROP DATABASE IF EXISTS nexus_restore;
CREATE DATABASE nexus_source WITH OWNER nexus_recovery_source TEMPLATE template0;
CREATE DATABASE nexus_restore WITH OWNER nexus_recovery_restore TEMPLATE template0;
SQL

DATABASE_OWNER_PROOF="$(psql "$RECOVERY_ADMIN_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 --field-separator='|' -c "
SELECT string_agg(datname || ':' || pg_get_userbyid(datdba), ',' ORDER BY datname)
FROM pg_database
WHERE datname IN ('nexus_source', 'nexus_restore');
")"
if [[ "$DATABASE_OWNER_PROOF" != "nexus_restore:nexus_recovery_restore,nexus_source:nexus_recovery_source" ]]; then
  echo "recovery_database_owner_proof_failed" >&2
  exit 4
fi

mapfile -t RECOVERY_ADMIN_DATABASE_URLS < <(
  RECOVERY_ADMIN_NATIVE_URL="$RECOVERY_ADMIN_NATIVE_URL" python - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit

parsed = urlsplit(os.environ["RECOVERY_ADMIN_NATIVE_URL"])
for database in ("nexus_source", "nexus_restore"):
    print(urlunsplit((parsed.scheme, parsed.netloc, "/" + database, "", "")))
PY
)
if [[ "${#RECOVERY_ADMIN_DATABASE_URLS[@]}" -ne 2 ]]; then
  echo "recovery_admin_database_url_derivation_failed" >&2
  exit 5
fi
SOURCE_ADMIN_NATIVE_URL="${RECOVERY_ADMIN_DATABASE_URLS[0]}"
RESTORE_ADMIN_NATIVE_URL="${RECOVERY_ADMIN_DATABASE_URLS[1]}"

psql "$SOURCE_ADMIN_NATIVE_URL" -X --set ON_ERROR_STOP=1 -c 'CREATE EXTENSION vector'
psql "$RESTORE_ADMIN_NATIVE_URL" -X --set ON_ERROR_STOP=1 -c 'CREATE EXTENSION vector'
SOURCE_VECTOR_PROOF="$(psql "$SOURCE_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 --field-separator='|' -c "SELECT extversion, pg_get_userbyid(extowner) FROM pg_extension WHERE extname = 'vector'")"
RESTORE_VECTOR_PROOF="$(psql "$RESTORE_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 --field-separator='|' -c "SELECT extversion, pg_get_userbyid(extowner) FROM pg_extension WHERE extname = 'vector'")"
if [[ -z "$SOURCE_VECTOR_PROOF" || "$SOURCE_VECTOR_PROOF" != "$RESTORE_VECTOR_PROOF" || "$SOURCE_VECTOR_PROOF" != *"|nexus_recovery_admin" ]]; then
  echo "recovery_vector_preinstall_proof_failed" >&2
  exit 6
fi

(cd backend && DATABASE_URL="$SOURCE_APP_URL" alembic upgrade head)
EXPECTED_HEAD="$(psql "$SOURCE_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
test -n "$EXPECTED_HEAD"

(cd backend && DATABASE_URL="$SOURCE_APP_URL" alembic downgrade -1)
OBSERVED_HEAD="$(psql "$SOURCE_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
set +e
python scripts/qualification/recovery/build_recovery_evidence.py migration-plan \
  --observed-head "$OBSERVED_HEAD" \
  --expected-head "$EXPECTED_HEAD" \
  --output "$EVIDENCE_ROOT/migration-repair-plan.json"
PLAN_EXIT=$?
set -e
test "$PLAN_EXIT" = "1"
test "$(jq -r '.action' "$EVIDENCE_ROOT/migration-repair-plan.json")" = "alembic_upgrade_head"
test "$(jq -r '.apply_authorized' "$EVIDENCE_ROOT/migration-repair-plan.json")" = "false"

(cd backend && DATABASE_URL="$SOURCE_APP_URL" alembic upgrade head)
test "$(psql "$SOURCE_NATIVE_URL" -XAt --set ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')" = "$EXPECTED_HEAD"

psql "$SOURCE_NATIVE_URL" -v ON_ERROR_STOP=1 <<'SQL'
INSERT INTO markets (code, name, country_code, is_active, created_at, updated_at)
VALUES ('QUAL-RCV-001', 'Synthetic Recovery Qualification', 'ZZ', TRUE, clock_timestamp(), clock_timestamp());
INSERT INTO teams (name, team_type, market_id, is_active, created_at, updated_at)
SELECT 'Synthetic Recovery Team', 'support', id, TRUE, clock_timestamp(), clock_timestamp()
FROM markets WHERE code = 'QUAL-RCV-001';
SQL
date -u +%Y-%m-%dT%H:%M:%S.%NZ > marker-committed-at.txt

python scripts/qualification/recovery/build_recovery_evidence.py snapshot \
  --database-url "$SOURCE_APP_URL" \
  --marker-code "$QUALIFICATION_MARKER" \
  --output "$EVIDENCE_ROOT/source-snapshot.json"

POSTGRES_NATIVE_URL="$SOURCE_NATIVE_URL" \
  bash scripts/deploy/backup_postgres.sh "$EVIDENCE_ROOT/backups"
BUNDLE="$(find "$EVIDENCE_ROOT/backups" -mindepth 1 -maxdepth 1 -type d -name 'helpdesk_*' -print -quit)"
test -n "$BUNDLE"
date -u +%Y-%m-%dT%H:%M:%S.%NZ > backup-completed-at.txt
BACKUP_SHA256="$(jq -r '.archive_sha256' "$BUNDLE/backup_manifest.json")"

date -u +%Y-%m-%dT%H:%M:%S.%NZ > restore-started-at.txt
ROLLBACK_CONFIRM=I_UNDERSTAND \
POSTGRES_NATIVE_URL="$RESTORE_NATIVE_URL" \
ROLLBACK_STATUS_FILE="$EVIDENCE_ROOT/rollback-result.json" \
  bash scripts/deploy/rollback_release.sh "$BUNDLE"
date -u +%Y-%m-%dT%H:%M:%S.%NZ > restore-completed-at.txt
test "$(jq -r '.database_restored' "$EVIDENCE_ROOT/rollback-result.json")" = "true"

python scripts/qualification/recovery/build_recovery_evidence.py snapshot \
  --database-url "$RESTORE_APP_URL" \
  --marker-code "$QUALIFICATION_MARKER" \
  --output "$EVIDENCE_ROOT/restored-snapshot.json"

python scripts/qualification/recovery/build_recovery_evidence.py compare \
  --source "$EVIDENCE_ROOT/source-snapshot.json" \
  --restored "$EVIDENCE_ROOT/restored-snapshot.json" \
  --output "$EVIDENCE_ROOT/recovery-evidence.json" \
  --source-sha "$SOURCE_SHA" \
  --backup-sha256 "$BACKUP_SHA256" \
  --marker-committed-at "$(cat marker-committed-at.txt)" \
  --backup-completed-at "$(cat backup-completed-at.txt)" \
  --restore-started-at "$(cat restore-started-at.txt)" \
  --restore-completed-at "$(cat restore-completed-at.txt)" \
  --rto-target-seconds "$RTO_TARGET_SECONDS" \
  --rpo-target-seconds "$RPO_TARGET_SECONDS"

test "$(jq -r '.status' "$EVIDENCE_ROOT/recovery-evidence.json")" = "pass"
