#!/usr/bin/env bash
set -Eeuo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set to a disposable non-production PostgreSQL database}"
case "$DATABASE_URL" in
  postgresql://*|postgresql+psycopg://*|postgres://*) ;;
  *) echo "DATABASE_URL must be PostgreSQL for this gate. Refusing non-PostgreSQL URL." >&2; exit 2 ;;
esac
case "$DATABASE_URL" in
  *prod*|*production*) echo "Refusing suspicious production-like DATABASE_URL" >&2; exit 2 ;;
esac
if [ "${APP_ENV:-test}" = "production" ]; then
  echo "Refusing APP_ENV=production" >&2
  exit 2
fi
if [ "${POSTGRES_GATE_CONFIRM_EMPTY_DB:-}" != "1" ]; then
  echo "Refusing to run PostgreSQL gate without POSTGRES_GATE_CONFIRM_EMPTY_DB=1" >&2
  echo "This gate runs tests that delete rows from ticket/background-job tables; use only a disposable empty release-gate DB." >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../.." ] && [ -d "$SCRIPT_DIR/../../.git" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -d ".git" ]; then
  REPO_ROOT="$(pwd)"
else
  echo "Cannot locate repository root" >&2
  exit 2
fi
cd "$REPO_ROOT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${OUT:-$REPO_ROOT/release_evidence_${STAMP}}"
mkdir -p "$OUT/command_outputs"
export APP_ENV="${APP_ENV:-test}"
export PYTHONPATH="$REPO_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"

{
  echo "UTC: $(date -u)"
  echo "DATABASE_URL is PostgreSQL and intentionally not printed."
  python --version
  python - <<'PY'
from sqlalchemy.engine import make_url
import os
url = make_url(os.environ['DATABASE_URL'])
db_name = url.database or ''
allowed_markers = ('test', 'testing', 'release_gate', 'release-gate', 'gate', 'ci', 'tmp', 'temporary')
if not any(marker in db_name.lower() for marker in allowed_markers):
    raise SystemExit(f"Refusing database name that does not look disposable: {db_name!r}")
print(f"PostgreSQL target database accepted as disposable by name: {db_name}")
PY
  git rev-parse HEAD
  git status --short
} 2>&1 | tee "$OUT/command_outputs/09_postgres_environment.log"

{
  echo "===== Alembic current before ====="
  (cd backend && alembic current || true)
  echo "===== Alembic upgrade head ====="
  (cd backend && alembic upgrade head)
  echo "===== Alembic current after ====="
  (cd backend && alembic current)
} 2>&1 | tee "$OUT/command_outputs/09_postgres_alembic_upgrade.log"

{
  echo "===== Disposable DB critical table emptiness check ====="
  python - <<'PY'
from sqlalchemy import inspect, text
from app.db import engine
critical_tables = [
    'tickets',
    'ticket_events',
    'background_jobs',
    'webchat_fast_idempotency',
]
inspector = inspect(engine)
violations = []
with engine.connect() as conn:
    for table in critical_tables:
        if not inspector.has_table(table):
            print(f"{table}: table_missing_after_migration")
            violations.append((table, 'missing'))
            continue
        count = conn.execute(text(f"select count(*) from {table}")).scalar_one()
        print(f"{table}: {count}")
        if count:
            violations.append((table, count))
if violations:
    raise SystemExit(f"Refusing to run destructive handoff tests against non-empty DB: {violations}")
PY
} 2>&1 | tee "$OUT/command_outputs/09_postgres_empty_db_guard.log"

{
  echo "===== PostgreSQL handoff worker E2E ====="
  (cd backend && pytest -q tests/test_webchat_handoff_snapshot_worker.py tests/test_webchat_fast_reply_api.py)
} 2>&1 | tee "$OUT/command_outputs/10_postgres_handoff_worker_e2e.log"

echo "PostgreSQL handoff gate evidence written to: $OUT"
