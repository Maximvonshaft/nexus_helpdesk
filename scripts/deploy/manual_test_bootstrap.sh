#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/backend"
export PYTHONPATH="$ROOT_DIR/.pydeps${PYTHONPATH:+:$PYTHONPATH}"
set -a
. ./.env.local-manual
set +a
python3 scripts/validate_production_readiness.py || true
python3 "$ROOT_DIR/.pydeps/bin/alembic" upgrade head
python3 scripts/init_dev_db.py
python3 scripts/check_openclaw_connectivity.py || true
