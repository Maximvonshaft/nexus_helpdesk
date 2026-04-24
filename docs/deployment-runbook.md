# NexusDesk Deployment Runbook

## Service roles

- `app`: FastAPI API and SPA host.
- `worker`: outbound queue dispatcher and general background jobs.
- `sync-daemon`: dedicated OpenClaw transcript sync worker.
- `event-daemon`: OpenClaw event ingestion loop with heartbeat.
- `nginx`: public reverse proxy, metrics restriction, health checks.

## Source of truth

- `webapp/` is the current frontend source of truth.
- `frontend_dist/` and `webapp/dist/` are build artifacts and must not be committed.
- `frontend/` is legacy fallback only until the React webapp is fully signed off.

## Deployment modes

- `local_gateway`: app container reaches OpenClaw bridge through `host.docker.internal` or localhost-equivalent routing.
- `remote_gateway`: app reaches a remote OpenClaw gateway by `OPENCLAW_MCP_URL` and token/password files.
- `disabled`: OpenClaw checks are disabled and should not be used for production support.

## Safe update flow

```bash
bash scripts/deploy/safe_update_server.sh
bash scripts/deploy/preflight.sh
bash scripts/deploy/backup_postgres.sh ./backups
bash scripts/deploy/run_migrations.sh
docker compose -f deploy/docker-compose.cloud.yml up -d app worker sync-daemon event-daemon nginx
curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1/readyz
```

## Rollback flow

```bash
export ROLLBACK_CONFIRM=I_UNDERSTAND
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/helpdesk'
export OLD_IMAGE_TAG='nexusdesk/helpdesk:previous'
bash scripts/deploy/rollback_release.sh ./backups/helpdesk_YYYYMMDD_HHMMSS.sql.gz
```

## Termius / phone operation

Use `tmux`, `screen`, or `nohup` for long commands. Do not run migration or build commands in a fragile mobile SSH session without a persistent terminal.

## Files to protect on servers

Never overwrite these blindly:

- `deploy/.env.prod`
- `deploy/docker-compose.server.yml`
- local Nginx overrides
- local secrets and token files
- database volumes and backups
