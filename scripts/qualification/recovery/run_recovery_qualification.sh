#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_APP_URL:?SOURCE_APP_URL is required}"
: "${SOURCE_NATIVE_URL:?SOURCE_NATIVE_URL is required}"
: "${RESTORE_APP_URL:?RESTORE_APP_URL is required}"
: "${RESTORE_NATIVE_URL:?RESTORE_NATIVE_URL is required}"
: "${SOURCE_SHA:?SOURCE_SHA is required}"
: "${QUALIFICATION_MARKER:=QUAL-RCV-001}"
: "${RTO_TARGET_SECONDS:=120}"
: "${RPO_TARGET_SECONDS:=60}"

EVIDENCE_ROOT="${EVIDENCE_ROOT:-artifacts/recovery}"
mkdir -p -- "$EVIDENCE_ROOT"

psql -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DROP DATABASE IF EXISTS nexus_source;
DROP DATABASE IF EXISTS nexus_restore;
CREATE DATABASE nexus_source;
CREATE DATABASE nexus_restore;
SQL

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
