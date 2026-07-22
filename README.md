# Nexus OSR / NexusDesk

Nexus is a Conversation-first customer-operations runtime for logistics support. Public channels, Agent execution, operator queues, governed Tickets, actions, knowledge, runtime evidence and management drill-down converge into one backend and one operator console.

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

The former static `frontend/`, Support Console product, parallel shared UI/API layers, custom UI kit, CSS token system and parallel WebSocket workspace authority are retired and must not be restored.

The customer-side widget under `backend/app/static/webchat/` is a separate public channel surface. It is not a second operator product.

## Source layout

- `backend/app/api` — FastAPI routes for authentication, Conversations, Tickets, channels, runtime and integrations.
- `backend/app/services` — canonical policy, scope, orchestration, storage, jobs, Agent Runtime and provider services.
- `backend/app/models.py` and registered model modules — SQLAlchemy domain model.
- `backend/alembic` — the only executable schema-migration authority.
- `backend/scripts/run_worker_supervised.py` — production Worker supervision entrypoint.
- `backend/scripts/run_worker.py` — internal queue-loop implementation, never a deployment entrypoint.
- `webapp/` — the only React + TypeScript + Vite operator console source.
- `frontend_dist/` — generated SPA output; intentionally not tracked.
- `deploy/` — controlled compose and proxy configuration.
- `config/architecture/service-authority.v1.json` — machine-readable backend responsibility ownership.
- `scripts/qualification/route_authority.py` — FastAPI method/path collision gate.
- `scripts/verify_repository.py` — repository verification authority.

## Conversation and Ticket model

Conversation is the live communication identity. Ticket is optional durable work.

```text
Customer message
→ Conversation
→ governed Agent or operator handling
→ optional Handoff
→ optional Ticket only when durable follow-up is required
→ persisted business outcome
```

New WebChat initialization must not create a Ticket. Historical ticket-backed Conversations execute through the same message, Agent, policy and operator authorities as ticketless Conversations.

Canonical WebChat authorities are:

- session identity: `backend/app/services/webchat_session_identity.py`
- initialization: `backend/app/services/conversation_first_service.py`
- visitor messages/actions: `backend/app/services/webchat_message_service.py`
- stable application facade: `backend/app/services/webchat_service.py`
- Agent orchestration: `backend/app/services/webchat_ai_orchestration_service.py`
- Agent reply execution/persistence: `backend/app/services/webchat_ai_service.py`
- operator read/reply: `backend/app/services/conversation_operator_service.py`

A direct model CLI, standalone Auto Reply Job, channel-specific Agent loop or separate ticketless reply service is forbidden.

## Runtime model

Run the backend locally:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Controlled production topology uses dedicated supervised Workers:

- `worker-outbound-controlled` → `run_worker_supervised.py --queue outbound`
- `worker-background-controlled` → `run_worker_supervised.py --queue background`
- `worker-webchat-ai-controlled` → `run_worker_supervised.py --queue webchat-ai`
- `worker-handoff-snapshot-controlled` → `run_worker_supervised.py --queue handoff-snapshot`

A deployment unit may not invoke `run_worker.py` directly or run `--queue all`.

## Operator journey

```text
Login
→ server-authorized scope
→ unified queue
→ Conversation, Ticket and evidence
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

## Retired persistence and work

The former channel persistence boundary is removed from application code and current schema. Migration `20260720_0063` preserves historical data in a hash-verified rollback archive and removes retired tables and columns.

Migration `20260722_0074` terminates persisted pending or processing jobs from the retired standalone `auto_reply.send_update` execution path. The removed model CLI and job type must not be restored.

## Voice capability authority

Voice is split into two explicit, non-overlapping capabilities:

- Human WebCall: `WEBCHAT_HUMAN_CALL_ENABLED`, owned by `api/webchat_voice.py` and `webchat_voice_service.py`.
- Live AI Voice: `WEBCHAT_LIVE_AI_VOICE_ENABLED`, owned by `api/webchat_live_voice.py` and `live_voice_orchestration_service.py`.

`WEBCHAT_VOICE_ENABLED` is compatibility-only. Production activation must use the explicit flags and deployment evidence; source-code presence is not production enablement.

## Frontend verification

```bash
cd webapp
npm ci --ignore-scripts
npm run verify
npm run e2e
```

`npm run verify` executes architecture checks, the single-transport gate, lint, type checking, unit contracts and the production build.

## Canonical repository verification

Remote execution is owned by exactly one immutable, read-only workflow: `.github/workflows/canonical-acceptance.yml`. It checks one exact event Head and delegates policy to repository-owned scripts. No feature may add a second workflow or bypass the required gate.

```bash
python scripts/verify_repository.py
```

Structure-only verification:

```bash
python scripts/verify_repository.py --static-only
```

The verifier rejects:

- a second frontend, shell, navigation, HTTP transport, UI or status authority;
- an undeclared backend responsibility, independently callable private core or business-bearing facade;
- duplicate FastAPI method + normalized-path registrations;
- runtime schema DDL or executable SQL outside Alembic;
- direct model CLI execution or a retired standalone AI Job;
- ticket-mandatory WebChat initialization or a second Agent reply service;
- legacy Lite response mode;
- unsupervised or `queue=all` deployment entrypoints;
- retired paths, stale current-state documents and unreachable frontend modules;
- a second GitHub Actions workflow or Actions-only governance authority.

## Migration policy

Alembic is the sole schema-mutation authority. Migrations must be linear, deterministic and fail closed. The current head is derived from `alembic heads` and immutable release evidence; documentation must not hard-code a moving revision as current truth.

Historical origin bindings are not assigned a guessed country. Production bindings without an explicit country remain unavailable until corrected.

## Production safety

Production requires:

- PostgreSQL `DATABASE_URL`;
- strong secret material supplied through approved secret authorities;
- `AUTO_INIT_DB=false` and `SEED_DEMO_DATA=false`;
- no dev authentication, legacy token transport or manual startup bypass;
- explicit WebCall and Live AI Voice activation;
- generated `frontend_dist`;
- explicit Provider routing, traffic mode, fallback and kill switch;
- supervised Worker progress and `/healthz` / `/readyz` success;
- immutable candidate, migration rehearsal, smoke evidence and rollback plan.

Do not deploy directly from a code-consolidation branch. Never use destructive Git cleanup against a live server directory before preserving environment files, data, attachments and server-only overrides.
