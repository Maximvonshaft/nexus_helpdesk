#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "===== Backend gate ====="
cd backend
python3 -m compileall app scripts
alembic heads
alembic upgrade head
pytest -q tests/test_deploy_contracts.py
pytest -q tests/test_outbound_message_semantics.py
pytest -q tests/test_production_dispatch_gates.py
pytest -q tests/test_outbound_semantics_single_source.py
pytest -q tests/test_webchat_runtime_idempotency.py
pytest -q tests/test_webchat_incremental_poll.py
pytest -q tests/test_webchat_rate_limit_tenant_scope.py
pytest -q tests/test_webchat_token_lifecycle.py
pytest -q tests/test_webchat_widget_runtime.py
pytest -q tests/test_migration_drift_gate.py
pytest -q tests

cd "$ROOT_DIR"
if [ -d webapp ]; then
  echo "===== Frontend gate ====="
  cd webapp
  npm run typecheck
  npm run build
  npm run lint
fi

cd "$ROOT_DIR"
echo "===== Deploy gate ====="
bash scripts/deploy/check_deploy_contract.sh
docker compose --env-file deploy/.env.prod.local-postgres.example -f deploy/docker-compose.server.local-postgres.yml config
docker compose --env-file deploy/.env.prod.external-postgres.example -f deploy/docker-compose.server.external-postgres.yml config
docker compose --env-file deploy/.env.prod.external-postgres.example -f deploy/docker-compose.server.external-postgres.yml build app worker

echo "===== PR27 closure validation passed ====="
