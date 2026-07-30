# Phase 1 production launch: WebChat text

## Decision

The first production release is a narrow but real customer-support product:

- public WebChat text intake;
- authenticated operator Workspace;
- manual human ownership and replies;
- Ticket-as-Case, Conversation, Handoff, Queue and audit persistence;
- PostgreSQL-backed health, readiness, Metrics and supervised Worker operation.

The following capabilities remain disabled until their own real-environment qualification is complete:

- AI automatic replies;
- WebCall, Voice and LiveKit;
- WhatsApp Meta/Baileys and media;
- inbound mailbox synchronization and outbound Email;
- Speedaf Provider writes and callbacks;
- Operations dispatch.

This is a production GO path for WebChat text only. It does not authorize any disabled capability.

## Exact candidate

Use one immutable image built from the exact source commit that passed Canonical Acceptance. The controlled environment must bind:

```text
CONTROLLED_IMAGE=name@sha256:<digest>
IMAGE_TAG=<same exact digest reference>
GIT_SHA=<accepted 40-hex source SHA>
FRONTEND_BUILD_SHA=<same source SHA>
EXPECTED_MIGRATION_HEAD=<single executable Alembic head>
```

Do not copy an old SHA or migration revision from documentation. Obtain them from the accepted candidate and executable migration graph.

## Configuration

Create an untracked controlled environment from the example matching the selected database topology. At minimum, preserve these fail-closed values:

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
EMAIL_MAILBOX_SYNC_ENABLED=false

WHATSAPP_ENABLED=false
WHATSAPP_EMBEDDED_SIGNUP_ENABLED=false
WHATSAPP_MEDIA_ENABLED=false
WHATSAPP_MEDIA_SCANNER=disabled

SPEEDAF_MCP_ENABLED=false
SPEEDAF_TRACK_QUERY_ENABLED=false
SPEEDAF_WORK_ORDER_CREATE_ENABLED=false
SPEEDAF_UPDATE_ADDRESS_ENABLED=false
SPEEDAF_CANCEL_ENABLED=false
SPEEDAF_VOICE_CALLBACK_ENABLED=false
OPERATIONS_DISPATCH_MODE=disabled
OPERATIONS_DISPATCH_ADAPTER=disabled
```

Set `ALLOWED_ORIGINS` to the exact operator-console origin and `WEBCHAT_ALLOWED_ORIGINS` to the exact customer-site origin. Wildcards and originless WebChat remain forbidden.

## Controlled deployment

Start the complete controlled topology even though AI and external dispatch are disabled. Keeping the same supervised service set allows one health, rollback and observability contract across every release.

External PostgreSQL:

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh up -d --no-build --pull always \
  migrate-controlled \
  app-controlled \
  worker-outbound-controlled \
  worker-background-controlled \
  worker-webchat-ai-controlled
```

Repository-managed local PostgreSQL:

```bash
NEXUS_DATABASE_TOPOLOGY=local \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled.local-postgres \
deploy/nexus-prod-compose.sh up -d --no-build --pull always \
  postgres-controlled \
  migrate-controlled \
  app-controlled \
  worker-outbound-controlled \
  worker-background-controlled \
  worker-webchat-ai-controlled
```

Disabled Workers may run their supervised loops, but their configuration must prevent Provider, AI, Voice, Email, WhatsApp, Speedaf and Operations effects.

## Runtime acceptance

Run the canonical runtime probe before exposing the Widget:

```bash
APP_DIR=/opt/nexus_helpdesk \
APP_URL=http://127.0.0.1:18095 \
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
bash scripts/probe_nexus_runtime.sh
```

The result must prove health, readiness, exact release identity, Alembic revision, upload persistence, Metrics authentication, queue semantics and fresh progress for all supervised Workers.

## Customer-domain acceptance

Do not open the Widget broadly until all checks pass on the real HTTPS domain:

1. `/healthz` and `/readyz` return HTTP 200 with the exact source, frontend and migration identity.
2. An approved customer origin can initialize the Widget and send a text message.
3. An unauthorized origin is rejected.
4. A scoped operator sees the same Conversation and takes ownership where required.
5. The operator sends a manual reply and the customer receives it through the polling fallback.
6. Reload/reconnect does not duplicate messages, lose ownership or expose another Tenant's data.
7. Closing/reopening follows the governed Ticket/Conversation state rules.

Manual WebChat replies are persisted local channel delivery and do not require the external outbound Worker.

## Initial operating envelope

For the first production window:

- one Tenant and one approved customer-site origin;
- a small named operator group;
- bounded traffic and an explicit support window;
- AI, Voice and every external Provider kept off;
- monitoring for HTTP 5xx, readiness, PostgreSQL connections, Worker progress, unassigned Conversation age and customer reply latency;
- verified database and upload backups before migration and every image change.

Do not advertise attachment/media support until its exact customer path has been accepted on the target environment.

## Rollback

If message intake, operator visibility or customer replies fail:

1. remove or disable the Widget at the customer site;
2. preserve logs, release identity and database evidence;
3. keep all Provider and outbound flags disabled;
4. run `scripts/deploy/rollback_release.sh` with the previous immutable image and matching controlled environment;
5. restore the database only through the separately qualified recovery path when data restoration is actually required.

## Expansion order

After WebChat text is stable, qualify capabilities independently:

1. WebSocket realtime transport;
2. AI reply canary;
3. outbound Email pilot;
4. WhatsApp text;
5. WhatsApp media;
6. WebCall and Voice;
7. Provider write operations.

Every expansion must preserve the same Customer, Conversation, Ticket-as-Case, Handoff, Queue and audit authorities. No temporary parallel product or alternate deployment topology is permitted.
