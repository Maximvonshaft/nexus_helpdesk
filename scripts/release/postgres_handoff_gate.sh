#!/usr/bin/env bash
set -Eeuo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set to a non-production PostgreSQL database}"
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
  echo "===== PostgreSQL handoff worker E2E ====="
  (cd backend && pytest -q tests/test_webchat_handoff_snapshot_worker.py tests/test_webchat_fast_reply_api.py)
} 2>&1 | tee "$OUT/command_outputs/10_postgres_handoff_worker_e2e.log"
