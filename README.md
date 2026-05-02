# NexusDesk / Nexus Helpdesk

NexusDesk is a case-centric customer operations runtime for logistics support. It unifies WebChat, tickets, customer conversations, OpenClaw inbound sync, outbound safety controls, attachments, audit history, and operator workflows.

## Current production posture

The current main production audit closure work is tracked in PR #27 on branch `fix/main-production-audit-closure`.

Key safety defaults:

```text
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OPENCLAW_CLI_FALLBACK_ENABLED=false
WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=false
```

Do not change these defaults during routine deploys.

## Source layout

- `backend/app` — FastAPI application, models, APIs, services, settings, and runtime logic.
- `backend/alembic` — schema migration chain.
- `backend/scripts` — worker, OpenClaw daemon, validation, and operational scripts.
- `backend/app/static/webchat/widget.js` — embeddable public WebChat widget.
- `webapp` — React / TypeScript / Vite operator console.
- `deploy` — production compose templates, env examples, nginx config, and deployment samples.
- `docs` — architecture, deployment, safety, migration, WebChat, OpenClaw, and incident runbooks.
- `scripts` — repository-level validation and deployment helper scripts.

## Quick local/backend verification

```bash
cd backend
python3 -m compileall app scripts
alembic heads
alembic upgrade head
pytest -q
```

## Frontend verification

```bash
cd webapp
npm run typecheck
npm run build
npm run lint
```

## Production audit closure validation

Run the full PR closure gate from the repository root:

```bash
bash scripts/validate_pr27_closure.sh
```

This runs backend checks, frontend checks, deployment contract checks, compose config checks, and docker build checks.

## Deployment entrypoints

Server deployment drift prevention is enforced through separated compose/env templates and the deploy contract check.

Choose exactly one deployment mode:

- Local PostgreSQL server or VM pilot:
  - `deploy/docker-compose.server.local-postgres.yml`
  - `deploy/.env.prod.local-postgres.example`
  - `docs/deploy-server-local-postgres.md`

- External or managed PostgreSQL:
  - `deploy/docker-compose.server.external-postgres.yml`
  - `deploy/.env.prod.external-postgres.example`
  - `docs/deploy-server-external-postgres.md`

Always run:

```bash
bash scripts/deploy/check_deploy_contract.sh
```

## Required documentation

- `docs/architecture.md`
- `docs/runbook-production.md`
- `docs/deploy-server-local-postgres.md`
- `docs/deploy-server-external-postgres.md`
- `docs/openclaw-integration.md`
- `docs/webchat-embed.md`
- `docs/outbound-safety.md`
- `docs/migration-policy.md`
- `docs/incident-playbook.md`

## Critical operating rules

1. WebChat ACK and safe fallback are local WebChat runtime records, not external provider sends.
2. OpenClaw inbound auto-sync and outbound dispatch are separate paths.
3. Production schema changes must be represented by Alembic migrations.
4. Migration must run before the new app and worker image handles traffic.
5. `deploy/.env.prod`, real tokens, real passwords, and real private URLs must never be committed.
6. Server-local runtime data such as `data/` and uploads must not be deleted during source updates.

## OpenClaw topology note

The current preferred architecture keeps OpenClaw local or edge-side and connects NexusDesk through the configured bridge or remote gateway. OpenClaw inbound sync may run while outbound dispatch remains disabled. Do not enable write bridge or outbound dispatch as part of routine production audit closure work.
