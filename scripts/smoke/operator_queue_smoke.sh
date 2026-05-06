#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/backend"

python -m compileall app scripts
alembic heads
alembic upgrade head
alembic downgrade -1
alembic upgrade head
pytest -q \
  tests/test_operator_queue.py \
  tests/test_operator_queue_api.py \
  tests/test_operator_queue_projection.py \
  tests/test_operator_queue_replay.py \
  tests/test_operator_queue_pagination.py \
  tests/test_operator_queue_audit.py
