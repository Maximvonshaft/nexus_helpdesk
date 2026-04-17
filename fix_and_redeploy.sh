#!/bin/bash
cd /home/vboxuser/.openclaw/workspace/tgbot/nexusdesk/helpdesk_suite_lite

echo ">> 1. Reverting Dockerfile COPY path..."
sed -i 's|/build/webapp/frontend_dist|/build/frontend_dist|g' Dockerfile

echo ">> 2. Fixing DATABASE_URL inside .env files for internal docker network..."
for f in backend/.env backend/.env.local-openclaw; do
    if [ -f "$f" ]; then
        sed -i 's|localhost:55432|postgres:5432|g' "$f"
        sed -i 's|127.0.0.1:55432|postgres:5432|g' "$f"
    fi
done

echo ">> 3. Rebuilding and Starting..."
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml build app worker sync-daemon event-daemon
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml up -d

echo ">> 4. Waiting for DB..."
sleep 5

echo ">> 5. Running DB Migrations..."
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml exec -T app bash -lc 'cd /app/backend && alembic upgrade head && AUTO_INIT_DB=false SEED_DEMO_DATA=false python scripts/init_dev_db.py'

echo ">> 6. Status check..."
curl -fsS http://127.0.0.1:8080/healthz || echo " (Health endpoint failed)"
echo ""
echo "Ad1213655702" | sudo -S docker compose -f deploy/docker-compose.local-openclaw.yml ps
