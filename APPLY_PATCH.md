# APPLY_PATCH

## 1. Backup first

From the repository root:

```bash
bash scripts/deploy/safe_update_server.sh || true
mkdir -p ../nexusdesk_backup_$(date +%Y%m%d_%H%M%S)
cp -a . ../nexusdesk_backup_$(date +%Y%m%d_%H%M%S)/repo
```

For production DB:

```bash
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/helpdesk'
bash scripts/deploy/backup_postgres.sh ./backups
```

## 2. Apply overlay

If using tar.gz:

```bash
tar -xzf nexusdesk-main-audit-closure-patch.tar.gz -C /tmp
rsync -a /tmp/nexusdesk-main-audit-closure-patch/ ./
```

If using zip:

```bash
unzip nexusdesk-main-audit-closure-patch.zip -d /tmp
rsync -a /tmp/nexusdesk-main-audit-closure-patch/ ./
```

## 3. Validate

```bash
python -m compileall backend/app backend/scripts
cd backend
alembic history || true
alembic heads || true
alembic upgrade head
python scripts/validate_production_readiness.py || true
cd ../webapp
npm ci
npm run typecheck
npm run build
cd ..
bash -n scripts/deploy/safe_update_server.sh
bash -n scripts/deploy/rollback_release.sh
docker compose -f deploy/docker-compose.cloud.yml config || true
```

## 4. Alembic long revision recovery

If a failed environment already has the old long revision in `alembic_version`, follow `docs/migration-troubleshooting.md` after taking a DB backup.

## 5. Rollback

Restore from the repo backup and DB backup. If using the helper:

```bash
export ROLLBACK_CONFIRM=I_UNDERSTAND
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/helpdesk'
export OLD_IMAGE_TAG='nexusdesk/helpdesk:previous'
bash scripts/deploy/rollback_release.sh ./backups/helpdesk_YYYYMMDD_HHMMSS.sql.gz
```
