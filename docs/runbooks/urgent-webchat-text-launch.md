# Urgent WebChat text launch

## Decision

Launch Nexus first as a narrow customer-support product:

- public WebChat text intake;
- operator Workspace and manual human replies;
- Ticket, Conversation, Handoff, Queue and audit persistence;
- health, readiness, metrics and PostgreSQL-backed operation.

Keep the following disabled until their separate repository and real-provider acceptance is complete:

- AI automatic replies;
- WebCall / Voice / LiveKit;
- WhatsApp Meta and Baileys;
- WhatsApp media;
- Email mailbox sync and outbound email;
- Speedaf write operations;
- Operations dispatch.

This is a real customer launch for WebChat text only. It is not authorization for any disabled capability.

## Required image identity

Deploy one immutable image built from the accepted source. The current source baseline at creation of this runbook is:

```text
GIT_SHA=991e5eea7d145ad19c3153efc20c4fe7ba60caf0
FRONTEND_BUILD_SHA=991e5eea7d145ad19c3153efc20c4fe7ba60caf0
EXPECTED_MIGRATION_HEAD=20260729_wa5_signup_checkpoint
```

`CONTROLLED_IMAGE` and `IMAGE_TAG` must be the same image reference using `name@sha256:<digest>`.

## Configuration

Start from `deploy/.env.controlled.example` or `deploy/.env.controlled.local-postgres.example` and set real host values. The following capability flags are mandatory for this launch:

```dotenv
COMPOSE_PROFILES=
APP_ENV=production
TENANT_RUNTIME_AUTHORITY_MODE=enforce
AUTO_INIT_DB=false
SEED_DEMO_DATA=false
ALLOW_DEV_AUTH=false

WEBCHAT_AI_ENABLED=false
WEBCHAT_AI_AUTO_REPLY_MODE=off
WEBCHAT_AI_RECONCILER_ENABLED=false

WEBCHAT_WS_ENABLED=false
WEBCHAT_WS_PUBLIC_ENABLED=false
WEBCHAT_WS_ADMIN_ENABLED=false
WEBCHAT_WS_BROKER=database

WEBCHAT_HUMAN_CALL_ENABLED=false
WEBCHAT_LIVE_AI_VOICE_ENABLED=false
WEBCHAT_VOICE_PROVIDER=mock

PROVIDER_RUNTIME_ENABLED=false
PROVIDER_RUNTIME_TRAFFIC_MODE=control
PROVIDER_RUNTIME_KILL_SWITCH=true
PROVIDER_RUNTIME_CANARY_PERCENT=0
PRIVATE_AI_RUNTIME_ENABLED=false

ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false

WHATSAPP_ENABLED=false
WHATSAPP_EMBEDDED_SIGNUP_ENABLED=false
WHATSAPP_MEDIA_ENABLED=false
WHATSAPP_MEDIA_SCANNER=disabled

EMAIL_MAILBOX_SYNC_ENABLED=false
SPEEDAF_MCP_ENABLED=false
SPEEDAF_TRACK_QUERY_ENABLED=false
SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
SPEEDAF_UPDATE_ADDRESS_ENABLED=false
SPEEDAF_CANCEL_ENABLED=false
SPEEDAF_VOICE_CALLBACK_ENABLED=false
OPERATIONS_DISPATCH_MODE=disabled
OPERATIONS_DISPATCH_ADAPTER=disabled
```

Set `ALLOWED_ORIGINS` to the operator-console origin and `WEBCHAT_ALLOWED_ORIGINS` to the exact customer website origin. Do not use `*` and do not enable originless WebChat.

## Minimum services

For this first launch, start only the services required by WebChat text and durable background operation:

```bash
docker compose \
  --env-file deploy/.env.controlled \
  -f deploy/docker-compose.controlled.yml \
  up -d migrate-controlled app-controlled worker-background-controlled
```

For the repository-managed local PostgreSQL topology:

```bash
docker compose \
  --env-file deploy/.env.controlled.local-postgres \
  -f deploy/docker-compose.controlled.yml \
  -f deploy/docker-compose.controlled-postgres.yml \
  up -d postgres-controlled migrate-controlled app-controlled worker-background-controlled
```

Do not start the WebChat AI Worker, outbound Worker, WhatsApp Sidecar or LiveKit Agent in this launch.

## Reverse proxy

Expose only the application through the existing HTTPS reverse proxy. Proxy the public website and operator console to `127.0.0.1:${CONTROLLED_APP_PORT}`. Preserve the original `Host` and trusted forwarding headers. Do not expose PostgreSQL, internal Worker ports or Sidecar ports.

## Five-step acceptance

Do not open the Widget to all customers until all five checks pass on the actual domain:

1. `GET /healthz` returns HTTP 200.
2. `GET /readyz` returns HTTP 200 and reports the exact source SHA, frontend SHA and migration head.
3. A customer opens the Widget on the approved website origin and sends a text message.
4. An authorized operator sees the same Conversation, accepts/takes ownership where required, and sends a manual reply.
5. The customer receives the reply through the polling fallback without duplicate messages, blank state or cross-tenant visibility.

The manual WebChat reply is a local persisted delivery and does not require the external outbound Worker.

## Initial operating limit

For the first production window:

- use one Tenant and one approved website origin;
- use a small named operator group;
- keep attachment/media upload out of the customer launch;
- keep AI, Voice and all external Providers off;
- monitor HTTP 5xx, `/readyz`, database connections, background Worker health and unassigned Conversation age;
- retain a tested database backup before migration and before each image change.

## Rollback

If message intake, operator visibility or customer replies fail:

1. remove or disable the Widget on the customer website;
2. keep Provider and outbound flags disabled;
3. preserve application and database logs;
4. stop the current application and Worker containers;
5. restore the previous immutable image and configuration snapshot;
6. do not roll back the database unless the migration has an explicitly tested downgrade path.

## Expansion sequence

After WebChat text is stable, enable capabilities independently in this order:

1. WebSocket realtime transport;
2. AI reply canary;
3. Email;
4. WhatsApp text;
5. WhatsApp media;
6. WebCall / Voice.

Each expansion must preserve the same Ticket, Customer, Conversation, Handoff and Queue authorities. No second product or temporary parallel implementation is permitted.
