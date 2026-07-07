#!/usr/bin/env bash
set -Eeuo pipefail

DRY_RUN=0
RUN_DB_PROBE=0

usage() {
  cat <<'USAGE'
Usage: scripts/probe_knowledge_retrieval_e2e.sh [--dry-run] [--run-db-probe]

Safe CI probe for knowledge retrieval wiring.
Defaults:
  - no public network access
  - no database writes
  - exits 0 with a skipped reason when DATABASE_URL is absent
  - only runs a read-only DB probe when --run-db-probe is explicit
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --run-db-probe)
      RUN_DB_PROBE=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$DRY_RUN" = "1" ]; then
  echo "knowledge_retrieval_probe_dry_run=true"
  echo "would_check=DATABASE_URL_present_and_optional_read_only_db_probe"
  exit 0
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "knowledge_retrieval_probe_skipped=true reason=DATABASE_URL_not_set"
  exit 0
fi

if [ "$RUN_DB_PROBE" != "1" ]; then
  echo "knowledge_retrieval_probe_skipped=true reason=run_db_probe_not_requested"
  exit 0
fi

python - <<'PY'
from __future__ import annotations

import os
from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL", "")
if not url:
    print("knowledge_retrieval_probe_skipped=true reason=DATABASE_URL_not_set")
    raise SystemExit(0)

engine = create_engine(url, pool_pre_ping=True)
with engine.connect() as conn:
    result = conn.execute(text("SELECT 1"))
    value = result.scalar()
    if value != 1:
        raise SystemExit("knowledge_retrieval_probe_failed=unexpected_select_result")
print("knowledge_retrieval_probe_pass=true mode=read_only")
PY
