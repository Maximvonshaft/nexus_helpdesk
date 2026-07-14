# NexusDesk / Helpdesk Suite Lite

NexusDesk is a case-centric customer operations runtime for logistics support. It connects public WebChat, a single customer-service console, governed operational actions, attachments, outbound delivery, audit history, and production-readiness checks into one backend and one supported frontend.

## Current Status

The default runtime is de-ExternalChannel.

- WebChat/demo does not require ExternalChannel.
- Docker images no longer install `@external-channel/codex`, the ExternalChannel CLI, MCP client, bridge server, sync daemon, or event daemon.
- ExternalChannel transport, deployment, sync, inbound auto-sync, event driver, bridge, and CLI fallback settings must remain disabled.
- Legacy `external_channel_*` database tables, schemas, and API names are retained only for backward compatibility with existing data contracts.
- New live reply generation routes through the configured server-side provider runtime or rule fallback.
- WhatsApp delivery is expected to use native sidecar or future cloud API modes, not the retired ExternalChannel bridge.

## Source Layout

- `backend/app/api` - FastAPI routes for auth, tickets, public WebChat, administration, integrations, and provider runtime.
- `backend/app/services` - ticket orchestration, permissions, storage, outbox dispatch, background jobs, provider runtime, WebChat services, Speedaf integrations, and compatibility helpers.
- `backend/app/models.py` - SQLAlchemy domain model.
- `backend/alembic` - schema migrations.
- `backend/scripts/run_worker.py` - queue worker entrypoint.
- `webapp/` - the only supported React + TypeScript + Vite frontend source.
- `deploy/docker-compose.server.yml` - current server/candidate compose template.
- `deploy/nginx/default.conf` - reverse proxy example.
- `scripts/deploy` and `scripts/smoke` - deployment, release, smoke, and readiness helpers.

## Frontend Authority

The authenticated product is one customer-service console:

- `/workspace` - customer queue, case facts, conversation, governed actions, outcomes, and completion state.
- `/knowledge` - customer-service facts, policies, and procedures.
- `/channels` - customer contact-channel availability.
- `/system` - bounded service-assurance status for authorized support leads.
- `/webchat` - compatibility redirect to `/workspace`; it does not mount a second operator console.

Frontend authority is enforced by:

- semantic tokens: `webapp/src/styles/tokens.css`;
- shared components: `webapp/src/components/ui/`;
- product register: `webapp/PRODUCT.md`;
- design register: `webapp/DESIGN.md`;
- machine contract: `webapp/design/frontend-product-foundation.v1.json`;
- architecture gate: `webapp/scripts/assert-frontend-convergence.mjs`.

The legacy static `frontend/`, duplicate Support Console, and duplicate `shared/ui` component tree have been removed. A missing modern build fails closed rather than selecting another frontend.

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
3. Fast reply or background jobs call the server-side reply router.
4. Server-side routing applies canary, kill-switch, and fallback rules.
5. Tracking facts use Speedaf-backed sources when enabled.
6. The customer-service console shows the conversation, ticket, case state, and governed actions.
7. Outbound dispatch uses native/email/sidecar adapters, guarded by production dispatch gates.

## ExternalChannel Retirement Rules

These settings are intentionally disabled and should not be re-enabled:

```env
EXTERNAL_CHANNEL_TRANSPORT=disabled
EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled
EXTERNAL_CHANNEL_SYNC_ENABLED=false
EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED=false
EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED=false
EXTERNAL_CHANNEL_BRIDGE_ENABLED=false
EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED=false
```

The codebase keeps some `ExternalChannel*` model/schema/API names because renaming persisted tables and contracts is a separate migration. Those paths are compatibility surfaces only; they do not start or call an ExternalChannel runtime.

## Frontend Build Policy

`webapp/` is the only frontend source of truth.

```bash
cd webapp
npm ci
npm run architecture
npm run typecheck
npm run lint
npm test
npm run build
npm run size-report
```

Docker production images build the SPA from source during image build. `frontend_dist/` is generated output and is intentionally not tracked.

## Production Safety

Production settings require:

- PostgreSQL `DATABASE_URL`.
- Strong `SECRET_KEY`.
- `AUTO_INIT_DB=false` and `SEED_DEMO_DATA=false`.
- No dev auth or legacy token transport.
- Explicit frontend build output.
- Disabled legacy ExternalChannel runtime settings.
- Provider runtime fallback rules for canary or kill-switch rollbacks.
- Health/readiness checks through `/healthz` and `/readyz`.

## Server Deployment Drift Prevention

Server deployments should keep runtime state separate from Git-tracked source.

Do not use `git reset --hard` or equivalent destructive cleanup against a live server directory until these paths are backed up and intentionally restored:

- `deploy/.env.prod`
- `data/`
- uploaded attachments / local storage roots
- server-only compose overrides or reverse-proxy files

Controlled deployment flow:

```bash
cd /opt/nexus_helpdesk
docker compose -f deploy/docker-compose.server.yml build
docker compose -f deploy/docker-compose.server.yml run --rm app alembic upgrade head
docker compose -f deploy/docker-compose.server.yml up -d
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

Do not deploy directly from frontend convergence work without candidate qualification, smoke tests, and a rollback runbook.

## Verification

Useful local checks:

```bash
python -m compileall backend/app
$env:PYTHONPATH='backend'; pytest -q backend/tests/test_production_settings_contract.py
cd webapp
npm ci
npm run architecture
npm run typecheck
npm run lint
npm test
npm run build
npm run size-report
npm run e2e
```

CI/GitHub Actions remains the preferred place for broad regression suites.