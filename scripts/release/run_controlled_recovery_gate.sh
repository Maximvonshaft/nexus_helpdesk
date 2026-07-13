#!/usr/bin/env bash
set -Eeuo pipefail

: "${RECOVERY_ADMIN_NATIVE_URL:?RECOVERY_ADMIN_NATIVE_URL required}"
: "${SOURCE_SHA:?SOURCE_SHA required}"
psql "${RECOVERY_ADMIN_NATIVE_URL}" -X --set ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE nexus_recovery_source LOGIN PASSWORD 'nexus_recovery_source'
  NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE nexus_recovery_restore LOGIN PASSWORD 'nexus_recovery_restore'
  NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS;
SQL
python -m unittest -v \
  scripts.qualification.recovery.test_recovery_contracts \
  scripts.qualification.recovery.test_recovery_review_regressions
bash -n \
  scripts/qualification/recovery/run_recovery_qualification.sh \
  scripts/deploy/backup_postgres.sh \
  scripts/deploy/rollback_release.sh
bash scripts/qualification/recovery/run_recovery_qualification.sh
rm -rf artifacts/recovery/backups
test "$(jq -r '.status' artifacts/recovery/recovery-evidence.json)" = "pass"
test "$(jq -r '.source_sha' artifacts/recovery/recovery-evidence.json)" = "${SOURCE_SHA}"
python scripts/security/scan_artifacts.py \
  --root . \
  --output artifacts/recovery/artifact-scan.json \
  artifacts/recovery/*.json
test "$(jq -r '.status' artifacts/recovery/artifact-scan.json)" = "pass"
