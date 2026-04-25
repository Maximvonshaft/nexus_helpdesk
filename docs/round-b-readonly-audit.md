# NexusDesk Round B Read-Only Audit

## Scope and access

This audit was prepared from the available GitHub repository context for `Maximvonshaft/nexus_helpdesk` and the local patch workspace. No production server files, secrets, `deploy/.env.prod`, `deploy/docker-compose.server.yml`, `data/`, or server-local `Dockerfile` were modified.

## Current branch / commit / status

Expected baseline from production handoff:

- Branch: `main`
- Commit: `c1672d5`
- Commit message: `test: add Round A OpenClaw E2E smoke harness (#7)`
- Allowed server-local difference: `M Dockerfile`

The patch package itself is an overlay and is intentionally not a git commit.

## Project structure summary

- `backend/app/main.py`: FastAPI application entrypoint, CORS, request middleware, health/readiness, router registration, SPA static serving.
- `backend/app/api/`: authenticated admin/ticket/lite/integration routes.
- `backend/app/models.py`: SQLAlchemy ORM for customers, tickets, comments, outbound messages, OpenClaw records, queue/runtime entities.
- `backend/app/services/`: ticket, SLA, message dispatch, safety, integration, OpenClaw runtime services.
- `backend/alembic/versions/`: database migrations.
- `webapp/src/`: React + Vite + TanStack Router admin UI.
- `scripts/smoke/`: Round A smoke harness and new Round B smoke entrypoint.

## Backend router structure

Existing routers observed in `backend/app/main.py`:

- admin
- auth
- customers
- files
- integration
- lookups
- lite
- stats
- tickets

Round B adds:

- `backend/app/api/webchat.py`
- registered in `backend/app/main.py` as `/api/webchat`
- static widget mount under `/webchat`

## Data model structure

Existing reusable models:

- `Ticket`: already supports `source`, `source_channel`, customer linkage, conversation state, comments, outbound messages, source chat id, preferred reply channel/contact.
- `TicketComment`: suitable for preserving visible customer/agent conversation transcript.
- `TicketOutboundMessage`: suitable for recording outbound Webchat reply delivery state.
- `Customer`: suitable for anonymous or identified webchat visitor profile.
- `ChannelAccount`: exists but is not required for Round B minimal public widget closure.
- `OpenClawTranscriptMessage`: OpenClaw-specific; intentionally not used as Round B source of truth.
- `outbound_safety`: existing decision function returns `SafetyDecision.reasons`, not `reason`.

Round B adds dedicated Webchat persistence:

- `WebchatConversation`
- `WebchatMessage`

This avoids risky modification of core ticket tables while linking each Webchat conversation to a real ticket.

## Alembic state

Expected current head before Round B:

- `20260421_gov_r4`

Round B adds migration:

- `20260425_round_b_webchat`

Tables created:

- `webchat_conversations`
- `webchat_messages`

## Frontend structure

Key files:

- `webapp/src/router.tsx`: central route tree; Round B adds `/webchat` route.
- `webapp/src/routes/workspace.tsx`: existing ticket workspace remains unchanged.
- `webapp/src/lib/api.ts`: central API client; Round B adds Webchat admin endpoints.
- `webapp/src/lib/types.ts`: Round B appends Webchat types.
- `webapp/src/layouts/AppShell.tsx`: main navigation; Round B adds `网站聊天` link.

## Existing capability summary

- Ticket creation and comments already exist.
- Outbound safety gate already exists and supports allow/review/block decisions.
- Round A smoke harness exists and should not be broken.
- No complete Webchat widget closure existed in the baseline.

## Webchat skeleton assessment

The baseline included early Webchat/service direction but did not provide the complete public widget → ticket intake → admin reply → visitor poll closure. Round B fills this gap.

## Minimal modification path

1. Add Webchat tables and ORM models.
2. Add public init/send/poll API.
3. Add admin list/thread/reply API.
4. Reuse `Ticket`, `TicketComment`, `TicketOutboundMessage`, and `outbound_safety`.
5. Add static widget and demo page under `/webchat`.
6. Add admin `/webchat` inbox page.
7. Add smoke and tests.

## Files intentionally not touched

- `deploy/.env.prod`
- `deploy/docker-compose.server.yml`
- `data/`
- server-local `Dockerfile` differences
- `.git/`
- real secrets/tokens/passwords
