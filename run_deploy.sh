#!/bin/bash
cd /home/vboxuser/.openclaw/workspace/tgbot/nexusdesk/helpdesk_suite_lite

echo "Ad1213655702" | sudo -S echo "Sudo authenticated." || { echo "Sudo failed"; exit 1; }

echo ">> Building images (--no-cache)..."
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml build --no-cache app worker sync-daemon event-daemon

echo ">> Starting containers..."
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml up -d postgres app worker sync-daemon event-daemon

echo ">> Waiting for DB..."
sleep 5

echo ">> Running migrations & initialization..."
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml exec -T app bash -lc 'cd /app/backend && alembic upgrade head && AUTO_INIT_DB=false SEED_DEMO_DATA=false python scripts/init_dev_db.py'

echo ">> Health Check..."
curl -fsS http://127.0.0.1:8080/healthz || echo " (Endpoint not ready yet)"
echo ""

echo ">> Container Status..."
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml ps
