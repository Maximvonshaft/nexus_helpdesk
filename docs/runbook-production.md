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
EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED=false
EXTERNAL_CHANNEL_TRANSPORT=disabled
EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled
EXTERNAL_CHANNEL_SYNC_ENABLED=false
EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED=false
EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED=false
EXTERNAL_CHANNEL_BRIDGE_ENABLED=false
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

## Legacy ExternalChannel Note

ExternalChannel runtime paths are retired. Do not enable bridge, MCP, CLI fallback, inbound auto-sync, sync daemon, or event driver settings. Existing `external_channel_*` tables and API names are compatibility surfaces only.
