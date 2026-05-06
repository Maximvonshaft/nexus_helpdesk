#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

APP_ENV_VALUE="${APP_ENV:-test}"
DATABASE_URL_VALUE="${DATABASE_URL:-sqlite:///./helpdesk_operator_queue_smoke.db}"

if [[ "$APP_ENV_VALUE" == "production" ]]; then
  echo "Refusing to run operator queue smoke with APP_ENV=production" >&2
  exit 2
fi

if [[ "$DATABASE_URL_VALUE" =~ ^postgres(ql)?:// ]] && [[ "${OPERATOR_QUEUE_SMOKE_ALLOW_NONLOCAL:-0}" != "1" ]]; then
  echo "Refusing to run operator queue smoke against PostgreSQL without OPERATOR_QUEUE_SMOKE_ALLOW_NONLOCAL=1" >&2
  exit 2
fi

export APP_ENV="$APP_ENV_VALUE"
export DATABASE_URL="$DATABASE_URL_VALUE"
export ALLOW_DEV_AUTH="${ALLOW_DEV_AUTH:-true}"
export SEED_DEMO_DATA="false"
export AUTO_INIT_DB="false"

cd backend
alembic heads
alembic upgrade head
python -m pytest -q \
  tests/test_operator_queue.py \
  tests/test_operator_queue_api.py \
  tests/test_operator_queue_projection.py \
  tests/test_operator_queue_replay.py \
  tests/test_operator_queue_pagination.py \
  tests/test_operator_queue_audit.py

if [[ "${OPERATOR_QUEUE_SMOKE_WITH_DOWNGRADE:-0}" == "1" ]]; then
  alembic downgrade -1
  alembic upgrade head
fi
