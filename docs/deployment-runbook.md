# NexusDesk Deployment Runbook

## Service roles

- `app`: FastAPI API and SPA host.
- `worker`: outbound queue dispatcher and general background jobs.
- `nginx`: public reverse proxy, metrics restriction, health checks.

## Source of truth

- `webapp/` is the current frontend source of truth.
- `frontend_dist/` and `webapp/dist/` are build artifacts and must not be committed.
- `frontend/` is legacy fallback only until the React webapp is fully signed off.

## Runtime modes

- WebChat Fast Reply uses `WEBCHAT_FAST_AI_PROVIDER=provider_runtime`.
- Legacy ExternalChannel runtime settings must remain disabled.
- External customer sends are fail-closed unless `ENABLE_OUTBOUND_DISPATCH=true` and a native/email provider is explicitly enabled.

## Safe update flow

```bash
bash scripts/deploy/safe_update_server.sh
bash scripts/deploy/preflight.sh
bash scripts/deploy/backup_postgres.sh ./backups
bash scripts/deploy/run_migrations.sh
docker compose -f deploy/docker-compose.server.yml up -d postgres app worker-outbound worker-background worker-webchat-ai worker-handoff-snapshot nginx
curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1/readyz
```

## Outbound Email pilot gate

Keep `ENABLE_OUTBOUND_DISPATCH=false`, `OUTBOUND_PROVIDER=disabled`, and `OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false` until the `/outbound-email` admin browser smoke and SMTP test-send gate pass. Follow [Outbound Email Production Pilot Runbook](runbooks/outbound-email-production-pilot.md) before enabling real Email dispatch.

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
