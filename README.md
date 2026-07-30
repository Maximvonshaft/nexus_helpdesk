# Nexus OSR / NexusDesk

Nexus is a Conversation-first customer-operations runtime for logistics support. Public channels, Agent execution, operator queues, governed Ticket-as-Case responsibility, actions, knowledge, runtime evidence and management drill-down converge into one backend and one operator console.

## Canonical product

The authenticated operator product has one implementation authority:

- source: `webapp/`;
- application shell: `webapp/src/app/AppShell.tsx`;
- navigation: `webapp/src/app/navigation.ts`;
- primary route: `/workspace`;
- supporting routes: `/knowledge`, `/channels`, `/runtime`, `/control-tower`;
- HTTP transport: `webapp/src/lib/apiClient.ts`;
- UI framework: `@mui/material`;
- theme: `webapp/src/theme/nexusTheme.ts` and `NexusThemeProvider.tsx`;
- operational presentation: `webapp/src/app/OperatorPresentation.tsx` and `webapp/src/domain/operationalPresentation.ts`.

The customer widget under `backend/app/static/webchat/` is a separate public channel surface, not a second operator product.

## Source layout

- `backend/app/api` — FastAPI routes for authentication, Conversations, Tickets, channels, runtime and integrations.
- `backend/app/services` — policy, scope, orchestration, storage, jobs, Agent Runtime and Provider services.
- `backend/app/models.py` plus registered model modules — SQLAlchemy domain model.
- `backend/alembic` — the only executable schema-migration authority.
- `backend/scripts/run_worker_supervised.py` — production Worker supervision entrypoint.
- `backend/scripts/run_worker.py` — internal queue loop, never a deployment entrypoint.
- `webapp/` — the only React/TypeScript operator console source.
- `frontend_dist/` — generated SPA output, intentionally untracked.
- `deploy/` — controlled Compose and proxy configuration.
- `config/architecture/service-authority.v1.json` — machine-readable backend responsibility ownership.
- `config/architecture/business-aggregate-authority.v1.json` — Conversation, Ticket-as-Case, Handoff and OperatorTask authority.
- `scripts/verify_repository.py` — repository verification authority.

## Conversation and Ticket-as-Case model

Conversation is the live communication identity. Ticket is the only durable customer-operations Case responsibility.

```text
Customer message
→ Conversation
→ governed Agent or operator handling
→ optional live Handoff
→ optional Ticket only when durable responsibility is required
→ persisted business outcome
```

New WebChat initialization does not create a Ticket. Historical ticket-backed Conversations execute through the same message, Agent, policy and operator authorities as ticketless Conversations.

- `WebchatConversation` owns live text, Voice and participation continuity.
- `Ticket` owns durable Case identity and responsibility.
- `WebchatHandoffRequest` owns the live human-handoff lifecycle.
- `OperatorTask` is a rebuildable queue projection and may not mutate source-domain state.
- No parallel Case model, table, identifier or ownership state machine is permitted.

Canonical WebChat authorities:

- session identity: `backend/app/services/webchat_session_identity.py`;
- initialization: `backend/app/services/conversation_first_service.py`;
- visitor messages/actions: `backend/app/services/webchat_message_service.py`;
- stable facade: `backend/app/services/webchat_service.py`;
- Agent orchestration: `backend/app/services/webchat_ai_orchestration_service.py`;
- Agent reply execution/persistence: `backend/app/services/webchat_ai_service.py`;
- operator read/reply: `backend/app/services/conversation_operator_service.py`;
- live Handoff commands: `backend/app/services/webchat_handoff_service_core.py`.

## Runtime model

Local backend:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Controlled production topology uses dedicated supervised Workers:

- `worker-outbound-controlled` → queue `outbound`;
- `worker-background-controlled` → queue `background`, including authoritative Handoff snapshot projection;
- `worker-webchat-ai-controlled` → queue `webchat-ai`.

A deployment unit may not invoke the internal Worker loop directly or run an all-queues Worker.

Production navigation:

- `docs/runbook-production.md`;
- `docs/runbooks/production-activation.md`;
- `docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md`;
- `deploy/nexus-prod-compose.sh`.

## Operator journey

```text
Login
→ server-authorized scope
→ unified queue
→ live Conversation or durable Ticket-as-Case
→ evidence and ownership
→ human decision
→ governed action
→ persisted operational result
→ customer communication
→ close, observe, repair or reopen
```

Technical request success is not business completion, customer notification or safe closure.

## Authorization model

```text
Role defaults / explicit overrides
→ effective capabilities
→ server-owned scope
→ canonical UI projection
```

Production API and service code do not infer access directly from role names. Central authorities are `permissions.py`, `scope_permissions.py` and `operator_queue_scope.py`.

## Voice capability authority

Voice is split into two non-overlapping capabilities:

- Human WebCall: `WEBCHAT_HUMAN_CALL_ENABLED`;
- Live AI Voice: `WEBCHAT_LIVE_AI_VOICE_ENABLED`.

Source-code presence is not activation. Each capability requires explicit configuration and real environment evidence.

## Verification

Frontend:

```bash
cd webapp
npm ci --ignore-scripts
npm run verify
npm run e2e
```

Repository:

```bash
python scripts/verify_repository.py
```

Structure-only verification:

```bash
python scripts/verify_repository.py --static-only
```

Remote execution is owned by the approved immutable workflows. The canonical acceptance workflow validates one exact event Head and delegates policy to repository-owned scripts.

The verifier rejects duplicate product/service authorities, route collisions, runtime schema DDL outside Alembic, direct model CLI execution, unsupervised Workers, duplicate Agent paths, retired deployment paths, stale operational documents and mutable supply-chain inputs.

## Migration policy

Alembic is the sole schema-mutation authority. Migrations are linear, deterministic and fail closed. Release evidence derives the current head from the executable migration graph; documentation does not hard-code a moving revision as current truth.

Historical origin bindings are never assigned a guessed country. Production bindings without explicit country authority remain unavailable until corrected.

## Production safety

Production requires:

- PostgreSQL with distinct migration/application/Worker identities;
- strong secrets supplied through approved secret authorities;
- automatic schema initialization and demo seeding disabled;
- no development authentication or legacy token transport;
- generated frontend assets;
- explicit Provider routing, traffic mode, fallback and kill switch;
- supervised Worker progress plus successful `/healthz` and `/readyz`;
- immutable candidate identity, migration rehearsal, storage backup, smoke evidence and rollback plan;
- real E2E evidence before any external capability is activated.

Do not deploy directly from an audit branch. Preserve environment files, data, attachments and server-only overrides before changing a live server checkout.
