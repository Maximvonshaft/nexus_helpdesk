#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/.deploy_backups/$STAMP}"
mkdir -p "$BACKUP_DIR"

echo "== NexusDesk safe update preflight =="
echo "repo=$ROOT_DIR"
echo "backup_dir=$BACKUP_DIR"

git status --short || true

for f in Dockerfile deploy/.env.prod deploy/docker-compose.server.yml deploy/docker-compose.cloud.yml deploy/nginx/default.conf backend/.env backend/.env.local-openclaw; do
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$f")"
    cp -a "$f" "$BACKUP_DIR/$f"
    echo "backed up $f"
  fi
done

echo
cat <<'EOF'
Next recommended manual steps:
1) Review git status and local production files before pulling/applying patches.
2) Run: bash scripts/deploy/preflight.sh
3) Run: bash scripts/deploy/backup_postgres.sh ./backups
4) Run migrations only after backup is confirmed: bash scripts/deploy/run_migrations.sh
5) Restart app/worker/sync/event services according to your deployment mode.

This script intentionally does not run git reset, delete files, or modify the database.
EOF
