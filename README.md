# NexusDesk / Helpdesk Suite Lite

NexusDesk is a case-centric customer operations runtime for logistics support. It connects public WebChat, operator ticket workflows, AI-assisted replies, attachments, outbound dispatch, audit history, and production readiness checks into one backend and operator console.

## Current Status

The default runtime is de-OpenClaw.

- WebChat/demo does not require OpenClaw.
- Docker images no longer install `@openclaw/codex`, the OpenClaw CLI, MCP client, bridge server, sync daemon, or event daemon.
- OpenClaw transport, deployment, sync, inbound auto-sync, event driver, bridge, and CLI fallback settings must remain disabled.
- Legacy `openclaw_*` database tables, schemas, and admin/API names are retained only for backward compatibility with existing data and UI contracts.
- New live reply generation routes through `provider_runtime`, `codex_app_server`, `openai_responses`, or `rule_engine` fallback.
- WhatsApp delivery is expected to use native sidecar or future cloud API modes, not the retired OpenClaw bridge.

## Source Layout

- `backend/app/api` - FastAPI routes for auth, tickets, WebChat, admin, runtime, integrations, and provider runtime.
- `backend/app/services` - ticket orchestration, permissions, storage, outbox dispatch, background jobs, provider runtime, WebChat AI, Speedaf integrations, and legacy compatibility helpers.
- `backend/app/models.py` - SQLAlchemy domain model.
- `backend/alembic` - schema migrations.
- `backend/scripts/run_worker.py` - queue worker entrypoint.
- `webapp/` - React + TypeScript + Vite operator console source.
- `frontend/` - legacy static fallback frontend.
- `frontend_dist/` - generated SPA build output, intentionally not tracked.
- `deploy/docker-compose.server.yml` - current server/candidate compose template.
- `deploy/nginx/default.conf` - reverse proxy example.
- `scripts/deploy` and `scripts/smoke` - deployment, release, smoke, and readiness helpers.

## Runtime Model

### API

Run the backend with:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Production should run through the Dockerfile and `deploy/docker-compose.server.yml`.

### Workers

The compose topology uses explicit workers:

- `worker-outbound` -> `--queue outbound`
- `worker-background` -> `--queue background`
- `worker-webchat-ai` -> `--queue webchat-ai`
- `worker-handoff-snapshot` -> `--queue handoff-snapshot`

The `legacy-worker` profile still exists for controlled compatibility runs with `--queue all`.

### WebChat/demo Business Chain

1. Public widget initializes a WebChat conversation.
2. Visitor messages are stored on the WebChat conversation and linked ticket.
3. Fast reply or background AI jobs call the provider router.
4. Provider Runtime applies routing, canary, kill-switch, and fallback rules.
5. Tracking facts use Speedaf-backed sources when enabled.
6. The operator console shows the conversation, ticket, runtime state, and handoff controls.
7. Outbound dispatch uses native/email/sidecar adapters, guarded by production dispatch gates.

## OpenClaw Retirement Rules

These settings are intentionally disabled and should not be re-enabled:

```env
OPENCLAW_TRANSPORT=disabled
OPENCLAW_DEPLOYMENT_MODE=disabled
OPENCLAW_SYNC_ENABLED=false
OPENCLAW_INBOUND_AUTO_SYNC_ENABLED=false
OPENCLAW_EVENT_DRIVER_ENABLED=false
OPENCLAW_BRIDGE_ENABLED=false
OPENCLAW_CLI_FALLBACK_ENABLED=false
```

The codebase keeps some `OpenClaw*` model/schema/API names because renaming persisted tables and frontend contracts is a separate migration. Those paths are compatibility surfaces only; they do not start or call an OpenClaw runtime.

## Frontend Build Policy

`webapp/` is the source of truth for the modern operator UI.

```bash
cd webapp
npm ci
npm run build
```

Docker production images build the SPA from source during image build. `frontend_dist/` is generated output.

## Production Safety

Production settings require:

- PostgreSQL `DATABASE_URL`.
- Strong `SECRET_KEY`.
- `AUTO_INIT_DB=false` and `SEED_DEMO_DATA=false`.
- No dev auth or legacy token transport.
- Explicit frontend build output.
- Disabled legacy OpenClaw runtime settings.
- Provider runtime fallback rules for canary or kill-switch rollbacks.
- Health/readiness checks through `/healthz` and `/readyz`.

## Server deployment drift prevention

Server deployments should keep runtime state separate from Git-tracked source.

Do not use `git reset --hard` or equivalent destructive cleanup against a live server directory until these paths are backed up and intentionally restored:

- `deploy/.env.prod`
- `data/`
- uploaded attachments / local storage roots
- server-only compose overrides or reverse-proxy files

Current controlled deployment flow:

```bash
cd /opt/nexus_helpdesk
docker compose -f deploy/docker-compose.server.yml build
docker compose -f deploy/docker-compose.server.yml run --rm app alembic upgrade head
docker compose -f deploy/docker-compose.server.yml up -d
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

Do not deploy directly from this cleanup work without a candidate compose, smoke test, and rollback runbook.

## Verification

Useful local checks:

```bash
python -m compileall backend/app
$env:PYTHONPATH='backend'; pytest -q backend/tests/test_production_settings_contract.py
cd webapp && npm run build
```

CI/GitHub Actions should remain the preferred place for broad regression suites.
