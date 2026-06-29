# Production Runbook

## Pre-deploy checks

Run before a production or staging rollout:

```bash
bash scripts/deploy/check_deploy_contract.sh
cd backend
alembic heads
alembic upgrade head
python scripts/check_model_migration_drift.py
pytest -q
```

Then validate the frontend and deploy shape:

```bash
cd webapp
npm run typecheck
npm run build
npm run lint
cd ..
docker compose --env-file deploy/.env.prod.example -f deploy/docker-compose.server.yml config
```

## Safety defaults

These defaults must remain false or disabled unless a dedicated outbound rollout has been approved:

```text
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OPENCLAW_CLI_FALLBACK_ENABLED=false
OPENCLAW_TRANSPORT=disabled
OPENCLAW_DEPLOYMENT_MODE=disabled
OPENCLAW_SYNC_ENABLED=false
OPENCLAW_INBOUND_AUTO_SYNC_ENABLED=false
OPENCLAW_EVENT_DRIVER_ENABLED=false
OPENCLAW_BRIDGE_ENABLED=false
WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=false
```

## Rollout order

1. Back up the current database.
2. Pull source and rebuild images.
3. Run Alembic migrations.
4. Start app and workers.
5. Check `/healthz` and `/readyz`.
6. Smoke test login, ticket list, ticket detail, WebChat, and outbound-disabled behavior.

## Rollback

1. Stop the new app and worker containers.
2. Restore the previous image or previous source checkout.
3. Restore database only if the migration is not forward-compatible and a backup exists.
4. Recheck `/healthz` and `/readyz`.

## Legacy OpenClaw Note

OpenClaw runtime paths are retired. Do not enable bridge, MCP, CLI fallback, inbound auto-sync, sync daemon, or event driver settings. Existing `openclaw_*` tables and API names are compatibility surfaces only.
