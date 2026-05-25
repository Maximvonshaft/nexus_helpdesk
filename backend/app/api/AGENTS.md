# backend/app/api/AGENTS.md — API Route Execution Contract

This contract applies to `backend/app/api/**`. API routes are the public and admin HTTP surface. Do not treat them as thin glue only; routes define authentication, authorization, idempotency, rate limit, request/response schema, and customer-visible behavior.

## 1. Mandatory inspection before route changes

Before editing any API route, inspect:

```text
backend/app/main.py                    router registration, middleware, security headers, SPA fallback
backend/app/settings.py                env guards, production restrictions, feature flags
backend/app/api/deps.py                current-user and auth dependencies
backend/app/services/permissions.py    capability checks
backend/app/models.py                  tables touched by route/service
backend/tests/                         exact route/security/contract tests
.github/workflows/backend-ci.yml       CI grouping for touched area
```

For a route change, also inspect the service it calls and the frontend API client if the route is used by the operator console:

```text
webapp/src/lib/api.ts
webapp/src/router.tsx
webapp/src/routes/**
```

## 2. Route registration rule

New routers must be included in `backend/app/main.py`. Verify:

```text
from .api.<module> import router as <name>_router
app.include_router(<name>_router)
```

Do not add orphaned route modules.

## 3. Authentication and authorization matrix

| Route class | Required gate |
|---|---|
| Admin routes under `/api/admin/**` | `get_current_user` plus appropriate capability check, usually `ensure_can_manage_runtime`, user/admin permissions, or equivalent |
| Operator ticket routes | current user plus ticket/customer/team visibility checks |
| Integration routes | integration client auth, scopes, rate limit, idempotency where write-like |
| Public WebChat routes | origin validation, rate limit, idempotency, no-store headers, safe request size/schema |
| Public WebCall/voice routes | visitor token/session token checks, feature flags, microphone/voice-specific runtime controls |
| Files/download routes | authenticated access plus ticket visibility before serving content |
| Metrics route | token-gated when enabled; never public in production |

Never rely on frontend hiding a button as an authorization control.

## 4. High-risk route files and contracts

### WebChat Fast Lane

File:

```text
backend/app/api/webchat_fast.py
```

Critical contracts:

```text
POST /api/webchat/fast-reply
POST /api/webchat/fast-reply/stream
OPTIONS /api/webchat/fast-reply
OPTIONS /api/webchat/fast-reply/stream
```

Do not regress:

```text
_validated_origin()
_public_cors_headers()
enforce_webchat_fast_rate_limit()
begin_webchat_fast_idempotency()
compute_request_hash()
compute_legacy_v1_request_hash_aliases()
get_or_create_fast_conversation()
append_fast_visitor_message()
extract_fast_business_state()
resolve_fast_routing_context()
decide_server_handoff_policy()
_lookup_fast_tracking_fact()
_tracking_fact_forced_reply_payload()
generate_webchat_fast_reply()
mark_webchat_fast_done()/mark_webchat_fast_failed()
```

Hard stops:

```text
No origin bypass in production.
No rate-limit bypass.
No idempotency bypass.
No unredacted tracking fact in customer-visible payload or prompt.
No unsafe AI/provider fallback.
No customer reply outside NexusDesk policy/audit path.
```

Required tests when touched:

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_webchat_events.py \
  backend/tests/test_webchat_events_realdb.py \
  backend/tests/test_webchat_events_rbac_realdb.py \
  backend/tests/test_webchat_polling_write_throttle.py \
  backend/tests/test_webchat_event_write_isolation.py \
  backend/tests/test_webchat_admin_conversations_query_count.py \
  backend/tests/test_webchat_fast_reply_api.py \
  backend/tests/test_webchat_fast_reply_provider_runtime.py \
  backend/tests/test_webchat_fast_ai_provider_router_phase1.py \
  backend/tests/test_webchat_fast_production_legacy_guard.py \
  backend/tests/test_webchat_stream_feature_flag.py
```

If some tests do not exist in the current checkout, state that explicitly and run the existing closest tests.

### WebCall AI / voice

Files:

```text
backend/app/api/webcall_ai.py
backend/app/api/admin_webcall_ai.py
backend/app/api/admin_webcall_ai_demo.py
backend/app/webcall_ai_schemas.py
backend/app/webchat_voice_config.py
backend/app/services/webcall_ai_production/**
```

Critical contracts:

```text
GET  /api/webcall-ai/runtime-config
POST /api/webcall-ai/sessions
GET  /api/webcall-ai/sessions/{session_public_id}
POST /api/webcall-ai/sessions/{session_public_id}/join-token
POST /api/webcall-ai/sessions/{session_public_id}/end
POST /api/webcall-ai/sessions/{session_public_id}/handoff
POST /api/webcall-ai/sessions/{session_public_id}/tracking-fallback
GET  /api/webcall-ai/sessions/{session_public_id}/events
```

Do not regress:

```text
visitor token validation
Idempotency-Key handling on session creation
managed_session() write boundaries
handoff safety path
tracking fallback storage
session event visibility
runtime config not exposing secrets
voice path permissions policy in backend/app/main.py
```

Required tests when touched:

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_webchat_voice_static_headers.py \
  backend/tests/test_webchat_voice_mock_ui_static.py \
  backend/tests/test_webchat_voice_p0_static.py \
  backend/tests/test_webchat_voice_api.py \
  backend/tests/test_webchat_voice_p0_gap_closure.py \
  backend/tests/test_webchat_voice_room_compensation.py \
  backend/tests/test_webcall_ai_production.py \
  backend/tests/test_webcall_ai_voice_loop.py
```

### Provider Runtime / Codex admin

Files:

```text
backend/app/api/admin_provider_runtime.py
backend/app/api/admin_provider_credentials.py
backend/app/services/provider_runtime/**
backend/app/services/ai_runtime/**
tools/nexus-codex-runtime/**
```

Critical contracts:

```text
GET   /api/admin/provider-runtime/status
PATCH /api/admin/provider-runtime/routing/webchat-fast-reply
GET   /api/admin/provider-credentials/codex/status
POST  /api/admin/provider-credentials/codex/authorize
POST  /api/admin/provider-credentials/codex/manual/start
POST  /api/admin/provider-credentials/codex/manual/complete
GET   /api/admin/provider-credentials/codex/callback
POST  /api/admin/provider-credentials/codex/device/start
GET   /api/admin/provider-credentials/codex/device/status/{session_id}
POST  /api/admin/provider-credentials/codex/device/poll/{session_id}
POST  /api/admin/provider-credentials/codex/refresh/{credential_id}
POST  /api/admin/provider-credentials/codex/revoke/{credential_id}
POST  /api/admin/provider-credentials/codex/disconnect/{credential_id}
```

Do not regress:

```text
ensure_can_manage_runtime()
admin-only smoke chat gate
OAuth callback high-entropy state handling
no token echoing in response/logs
credential encryption/custody
primary/fallback provider allowlists
codex requires OpenClaw fallback rule
kill switch/canary semantics
```

Required tests when touched:

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_provider_runtime_codex_oauth_webchat.py \
  backend/tests/test_provider_runtime_kill_switch_canary.py \
  backend/tests/test_provider_runtime_router_fallback_e2e.py \
  backend/tests/test_codex_smoke_chat.py \
  backend/tests/test_oauth_refresh_concurrency.py \
  backend/tests/test_webchat_codex_app_server_provider.py \
  backend/tests/test_webchat_codex_app_server_canary_observability.py
```

### Speedaf actions

Files:

```text
backend/app/api/speedaf_actions.py
backend/app/api/speedaf_cancel.py
backend/app/services/speedaf_*.py
backend/app/services/background_jobs.py
backend/scripts/run_worker.py
```

Do not enable write actions unless all are present:

```text
capability check
tenant/operator authorization
idempotency key or durable dedupe
audit log
feature flag default safe/off when appropriate
background job path when external side effect is async
rollback or compensation note
test coverage
```

## 5. Response schema rule

Do not change response shapes silently. If a frontend route uses the response, update all of:

```text
backend route/schema
backend tests
webapp/src/lib/types.ts
webapp/src/lib/api.ts
webapp route/component
webapp tests if present
```

## 6. Write-boundary rule

For database writes, prefer explicit transaction boundaries:

```python
with managed_session(db):
    ...
```

or an existing repository pattern for that route. Do not introduce hidden nested commits without explaining why in the PR.

## 7. Validation minimum

For any API route change:

```bash
set -Eeuo pipefail
PYTHONPATH=backend python -m compileall backend/app backend/scripts
PYTHONPATH=backend pytest -q <targeted tests>
```

Run full backend tests when the change touches auth, provider runtime, WebChat/WebCall, storage/files, queue/job behavior, migrations, or Speedaf actions.
