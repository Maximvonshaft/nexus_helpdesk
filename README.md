# Nexus OSR / NexusDesk

Nexus is a case-centric customer-operations runtime for logistics support. Public WebChat, operator queues, ticket workflows, governed actions, knowledge, channel operations, runtime evidence and management drill-down converge into one backend and one operator console.

## Canonical product

The authenticated operator product has one implementation authority:

- source: `webapp/`
- application shell: `webapp/src/app/AppShell.tsx`
- navigation: `webapp/src/app/navigation.ts`
- primary route: `/workspace`
- supporting routes: `/knowledge`, `/channels`, `/runtime`, `/control-tower`
- compatibility-only route: `/webchat`
- HTTP transport: `webapp/src/lib/apiClient.ts`
- UI framework: `@mui/material`
- theme: `webapp/src/theme/nexusTheme.ts`
- theme provider: `webapp/src/theme/NexusThemeProvider.tsx`
- bounded operator presentation: `webapp/src/app/OperatorPresentation.tsx`
- global CSS boundary: reset, font, accessibility and document-level foundations only
- operational status vocabulary: `webapp/src/domain/operationalPresentation.ts`

The former static `frontend/`, Support Console product, `shared/ui`, `shared/api`, `webapp/src/lib/api.ts`, custom UI kit, CSS token system and parallel WebSocket workspace authority are retired and must not be restored.

The customer-side public WebChat widget under `backend/app/static/webchat/` is a separate public surface. It is not a second operator product.

## Source layout

- `backend/app/api` — FastAPI routes for authentication, cases, WebChat, channels, runtime and integrations.
- `backend/app/services` — policy, scope, ticket orchestration, storage, jobs, provider runtime, WebChat AI and Speedaf integrations.
- `backend/app/models.py` — SQLAlchemy domain model.
- `backend/alembic` — the only executable schema-migration authority.
- `backend/scripts/run_worker.py` — queue worker entrypoint.
- `webapp/` — the only React + TypeScript + Vite operator console source.
- `frontend_dist/` — generated SPA output; intentionally not tracked.
- `deploy/` — compose and proxy configuration.
- `config/architecture/service-authority.v1.json` — machine-readable backend public/core/shim ownership.
- `scripts/qualification/route_authority.py` — generated FastAPI method/path authority table and collision gate.
- `scripts/verify_repository.py` — local end-to-end repository verification authority.

## Runtime model

Run the backend locally:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

The compose topology uses explicit workers:

- `worker-outbound` → `--queue outbound`
- `worker-background` → `--queue background`
- `worker-webchat-ai` → `--queue webchat-ai`
- `worker-handoff-snapshot` → `--queue handoff-snapshot`

The retained `legacy-worker` profile is compatibility-only and must not become a second runtime authority.

## Operator journey

```text
Login
→ server-authorized scope
→ unified queue
→ case and evidence
→ human decision
→ governed action
→ persisted operational result
→ customer communication
→ close, observe, repair or reopen
```

Technical request success must never be presented as business completion, customer notification or safe closure.

## Authorization model

Runtime authorization is capability-derived:

```text
Role defaults / explicit overrides
→ effective capabilities
→ server-owned scope
→ canonical UI projection
```

Production API and service code must not infer access directly from a role name. Central authorities are:

- `backend/app/services/permissions.py`
- `backend/app/services/scope_permissions.py`
- `backend/app/services/operator_queue_scope.py`

## Retired persistence boundary

The former channel persistence boundary is removed from application code and current schema. Migration `20260720_0063` preserves historical rows in a hash-verified rollback archive, projects ticket-linked evidence into canonical ticket events and attachments, and removes the retired tables and columns. No runtime route, worker, setting, model, or provider fallback may recreate that state.

## Canonical voice and telephony

LiveKit is the only real-time media plane for browser voice and SIP/PSTN. All participants join the same canonical Room: caller, governed AI Agent, human operator, recorder, transcriber and authorized supervisor.

Nexus remains the orchestration and system-of-record authority:

- `WebchatConversation` — the one interaction entity;
- `WebchatHandoffRequest.assigned_agent_id` and `WebchatConversation.active_agent_id` — the only human ownership authority;
- Agent Runtime and immutable Agent Release — AI decisions, knowledge and governed Tools;
- `OperatorAgentState` — online state, heartbeat, text/voice capacity and after-call work;
- `ChannelAccount(provider="voice")` with `VoiceChannelConfiguration` — the one telephone configuration product under `/channels`;
- Timeline, Evidence and Audit — durable operational proof;
- Ticket — created only when follow-up work is required, never as a call prerequisite.

The adaptive handoff journey uses existing governed Tools rather than a telephony-specific parallel runtime:

```text
support.availability
→ read scoped operator status, voice capacity, queue and evidence-based wait
→ explain available options to the customer
→ handoff.request.create when a live transfer is chosen
→ ticket.create only after exact, one-time customer confirmation when follow-up is required
```

Voice control is durable:

```text
API / Agent request
→ Voice Command outbox
→ worker-owned LiveKit/SIP execution
→ Provider or Room-controller event
→ idempotent projection
→ Timeline and Audit
```

The retired PCM/AudioWorklet `/webchat/live/ws` media edge, `LIVE_VOICE_UPSTREAM_*`, `nexus_media_edge`, parallel AI voice orchestration and independent Voice ownership must not be restored.

Repository support does not imply that a real DID, Carrier, SIP trunk or production LiveKit credential is active. Missing Provider configuration fails closed and cannot simulate successful PSTN operations.

## Frontend build

```bash
cd webapp
npm ci --ignore-scripts
npm run verify
```

`npm run verify` executes architecture checks, the single-transport gate, lint, type checking, contract tests and the production build. Browser journeys run separately with:

```bash
npm run e2e
```

## Canonical repository verification

Remote execution is owned by exactly one immutable, read-only workflow: `.github/workflows/canonical-acceptance.yml`. It checks the exact event Head and delegates verification policy to repository-owned scripts. No feature may add a second workflow or bypass the required gate.

Run the same repository verification locally:

```bash
python scripts/verify_repository.py
```

Structure-only verification:

```bash
python scripts/verify_repository.py --static-only
```

The verifier rejects:

- a second frontend or operator shell;
- a second navigation, transport, UI or status authority;
- backend public/core/shim ownership that diverges from the authority manifest;
- duplicate FastAPI method + normalized-path registrations;
- executable raw SQL migration paths outside Alembic;
- retired paths and unreachable frontend modules;
- a second GitHub Actions workflow or Actions-only governance authority;
- parallel voice media, ownership, presence, routing or Channel configuration authorities;
- loss of Runtime read/manage separation;
- loss of cancel-preview input binding;
- noncanonical Control Tower links.

## Migration policy

Alembic is the sole executable schema-mutation authority. Migrations are linear, reversible and fail closed. Manually executable SQL migrations under `ops/`, deployment directories or runbooks are forbidden.

The WebChat country authority migration is:

```text
20260713_0059 → 20260715_0060
```

Historical origin bindings are not assigned a guessed country. Production bindings without an explicit country remain unavailable until corrected.

## Production safety

Production requires:

- PostgreSQL `DATABASE_URL`;
- strong `SECRET_KEY`;
- `AUTO_INIT_DB=false`;
- `SEED_DEMO_DATA=false`;
- no dev authentication or legacy token transport;
- LiveKit URL, API key and API secret when voice is enabled;
- server-governed `ChannelAccount(provider="voice")`, DID, SIP trunk and Dispatch Rule mapping for PSTN;
- generated `frontend_dist` present;
- explicit Provider routing, fallback and kill-switch configuration;
- `/healthz` and `/readyz` passing.

Do not deploy directly from a code-consolidation branch. Deployment requires an explicit candidate, migration rehearsal, smoke evidence and rollback plan. Never use destructive Git cleanup against a live server directory before preserving environment files, data, attachments and server-only overrides.
