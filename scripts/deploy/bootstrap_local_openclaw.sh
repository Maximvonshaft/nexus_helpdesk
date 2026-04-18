#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found" >&2
  exit 1
fi

if ! command -v docker compose >/dev/null 2>&1; then
  echo "docker compose not found" >&2
  exit 1
fi

if ! command -v openclaw >/dev/null 2>&1; then
  echo "openclaw not found on PATH. Install and pair OpenClaw locally first." >&2
  exit 1
fi

cp -n backend/.env.local-openclaw.example backend/.env.local-openclaw >/dev/null 2>&1 || true

echo "[1/5] Starting local stack..."
docker compose -f deploy/docker-compose.local-openclaw.yml up -d --build postgres app worker sync-daemon event-daemon

echo "[2/5] Running migrations..."
docker compose -f deploy/docker-compose.local-openclaw.yml exec -T app bash -lc 'cd /app/backend && alembic upgrade head'

echo "[3/5] Seeding local demo data..."
docker compose -f deploy/docker-compose.local-openclaw.yml exec -T app bash -lc 'cd /app/backend && AUTO_INIT_DB=false SEED_DEMO_DATA=false python scripts/init_dev_db.py'

echo "[4/5] Application health..."
curl -fsS http://localhost:8080/healthz || true

echo "[5/6] Host bridge health..."
python backend/scripts/check_openclaw_bridge_health.py || true

echo "[6/6] OpenClaw MCP probe..."
docker compose -f deploy/docker-compose.local-openclaw.yml exec -T app bash -lc 'cd /app/backend && python scripts/check_openclaw_connectivity.py' || true

echo
echo "Local stack ready. Next steps:"
echo "  1) Ensure your local OpenClaw Gateway is running and paired"
echo "  2) Start the host bridge if not already running: python backend/scripts/run_openclaw_bridge_manual.py"
echo "  3) Login at http://localhost:8080"
echo "  4) Open 运营保障 -> 检查 OpenClaw 联调"
echo "  5) Verify outbound send uses the WS bridge before CLI fallback"
