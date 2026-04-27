# NexusDesk Production Hardening Fix Report

Branch: `fix/production-hardening-webchat-openclaw-outbound`
Base: `main` at `99ecd42537e914df5200ce82f11f8812173c6fc4`

## Scope

This branch applies a focused P0/P1 production-hardening patch for NexusDesk. The changes target public Webchat exposure, OpenClaw CLI fallback defaults, manager capability reduction, outbound dispatch recovery semantics, database-backed Webchat rate limiting, and CI production readiness gating.

## Completed fixes

### P0-1 Webchat CORS origin hardening

Files changed:
- `backend/app/settings.py`
- `backend/app/api/webchat.py`

What changed:
- Added `WEBCHAT_ALLOWED_ORIGINS`.
- Added `WEBCHAT_ALLOW_NO_ORIGIN`.
- Replaced public Webchat CORS origin reflection with allowlist validation.
- Removed the production path that returned `Access-Control-Allow-Origin: *`.
- Allowed localhost only for development/test/local.

Remaining configuration requirement:
- Production deployments must set `WEBCHAT_ALLOWED_ORIGINS` to the real customer website origins that may embed the widget.

### P0-2 Webchat visitor token transport hardening

Files changed:
- `backend/app/api/webchat.py`
- `backend/app/static/webchat/widget.js`

What changed:
- Widget polling now sends `X-Webchat-Visitor-Token` instead of putting `visitor_token` in the URL query string.
- Backend now prefers `X-Webchat-Visitor-Token`.
- Query token remains as a short compatibility path for old widgets.
- Widget no longer actively emits `?visitor_token=`.

### P0-3 Webchat database-backed rate limiting

Files changed:
- `backend/app/settings.py`
- `backend/app/services/webchat_rate_limit.py`
- `backend/app/api/webchat.py`
- `backend/alembic/versions/20260427_prod_hardening.py`

What changed:
- Added `WEBCHAT_RATE_LIMIT_BACKEND` with production default `database`.
- Added `WEBCHAT_RATE_LIMIT_WINDOW_SECONDS`.
- Added `WEBCHAT_RATE_LIMIT_MAX_REQUESTS`.
- Added `webchat_rate_limits` table.
- Added public Webchat API level database-backed limiter.
- Client IP resolution only trusts `X-Forwarded-For` when the direct client host is in `TRUSTED_PROXY_IPS`.

Known follow-up:
- `webchat_service.py` still contains its previous in-process limiter. Because API-level database limiter now runs before service handling, the public edge is hardened. A later cleanup PR should remove the old service-level memory limiter to avoid double limiting in local/test mode.

### P0-4 Manager capability reduction

Files changed:
- `backend/app/services/permissions.py`

What changed:
- Removed the following default capabilities from `UserRole.manager`:
  - `user.manage`
  - `channel_account.manage`
  - `ai_config.manage`
  - `runtime.manage`
  - `market.manage`
- Admin remains fully privileged.
- Capability overrides still allow explicit exception grants.

### P0-5 OpenClaw CLI fallback disabled by default

Files changed:
- `backend/app/settings.py`
- `backend/scripts/validate_production_readiness.py`

What changed:
- `OPENCLAW_CLI_FALLBACK_ENABLED` now defaults to `false`.
- Production startup fails if `OPENCLAW_CLI_FALLBACK_ENABLED=true`.
- Production readiness now reports fallback-enabled environments as unsafe.

### P0-6 Outbound duplicate-send risk reduction

Files changed:
- `backend/app/services/message_dispatch.py`
- `backend/alembic/versions/20260427_prod_hardening.py`

What changed:
- Added stable local idempotency semantics using `nexusdesk-outbound-{message.id}`.
- Stored the key in `provider_message_id` for current compatibility.
- Added the idempotency key into outbound audit/route payloads.
- Skips already-sent messages before dispatch.
- Commits after each processed message instead of one batch-level commit.
- Added helper to requeue dead outbound messages.
- Added migration column `provider_idempotency_key` as a forward-compatible schema hook, although the current implementation uses the existing `provider_message_id` field to avoid broader model/schema churn in this PR.

Known follow-up:
- OpenClaw bridge/CLI/MCP functions still need provider-native idempotency support. This PR makes local state and audit idempotency-ready but does not modify OpenClaw provider contracts.

### P1-4 CI production readiness gate

Files changed:
- `.github/workflows/production-readiness.yml`
- `backend/scripts/validate_production_readiness.py`

What changed:
- Added a strict `production-readiness-gate` workflow.
- The workflow runs migrations against PostgreSQL and runs production readiness with safe dummy production env values.
- Existing advisory checks are preserved.

### P1-5 Dead job/outbound requeue API implementation

Files changed:
- `backend/app/api/admin_queue.py`
- `backend/app/services/message_dispatch.py`

What changed:
- Added runtime-managed requeue endpoints in a new router:
  - `POST /api/admin/jobs/{job_id}/requeue`
  - `POST /api/admin/jobs/requeue-dead`
  - `POST /api/admin/outbound/{message_id}/requeue`
  - `POST /api/admin/outbound/requeue-dead`
- Added admin audit logging for requeue actions.

Important incomplete item:
- The router still needs to be registered in `backend/app/main.py`. The attempted tool call to update `main.py` was blocked by the execution environment safety filter. The code is present but the endpoints will not be exposed until `main.py` imports and includes `admin_queue_router`.

Manual patch needed:

```python
from .api.admin_queue import router as admin_queue_router
...
app.include_router(admin_queue_router)
```

Insert it next to the existing admin router registration.

## Partially completed / deferred items

### P1-1 OpenClaw auto-link account/market hardening

Status: deferred.

Reason:
- This requires deeper changes to `openclaw_bridge.py` and corresponding tests around session routing, account matching, market matching, and unresolved quarantine behavior.
- The file is large and high-risk. I avoided making a broad patch without local test execution.

Recommended next PR:
- Update `_find_matching_open_tickets()` to accept route context.
- Require account/channel/market disambiguation before auto-linking.
- Force unresolved quarantine when account context is absent in multi-account environments.

### P1-2 unresolved replay concurrency/idempotency hardening

Status: deferred.

Recommended next PR:
- Add replay state CAS or row lock.
- Block replay of `resolved` / `dropped` events.
- Add unique constraints for transcript and attachment references if missing.

### P1-3 OpenClaw attachment `persist_bytes` validation

Status: deferred.

Recommended next PR:
- Extend `StorageBackend.persist_bytes()` signature with allowed MIME/extension/max byte constraints.
- Apply the same restrictions to OpenClaw attachment persistence as normal upload flow.

### P1-6 Webchat AI high-risk mode

Status: settings added, behavior not fully wired.

What is already added:
- `WEBCHAT_AI_AUTO_REPLY_MODE=off|safe_ack|safe_ai`
- Production default is `safe_ack`.
- Readiness checks warn if production is not `off` or `safe_ack`.

Remaining implementation:
- `webchat_ai_service.py` must still short-circuit bridge generation in `safe_ack` mode and force high-risk intent to safe acknowledgement/review fallback.

## Migration

Added migration:
- `backend/alembic/versions/20260427_prod_hardening.py`
- Revision ID: `20260427_prod_hardening`
- Down revision: `20260410_0001`

Migration adds:
- `webchat_rate_limits`
- `ticket_outbound_messages.provider_idempotency_key`

## Test / validation status

Tooling limitation:
- I could modify the GitHub branch through the GitHub connector, but I could not clone the private repository into the execution container because external DNS/network access to GitHub was unavailable from the container.
- Therefore I did not run `pytest`, `alembic upgrade head`, `npm run build`, or Docker build locally.

Required validation before merge:

```bash
python -m compileall backend/app backend/scripts
pytest -q backend/tests
cd backend && alembic upgrade head
cd ../webapp && npm ci && npm run typecheck && npm run build
cd .. && docker build -t nexusdesk/helpdesk:hardening-check .
```

## Deployment configuration checklist

Production must configure:
- `WEBCHAT_ALLOWED_ORIGINS=https://your-customer-site.example`
- `WEBCHAT_RATE_LIMIT_BACKEND=database`
- `WEBCHAT_AI_AUTO_REPLY_MODE=safe_ack` or `off`
- `OPENCLAW_CLI_FALLBACK_ENABLED=false`
- `TRUSTED_PROXY_IPS` if using `X-Forwarded-For`
- `ALLOWED_ORIGINS` without localhost
- `DATABASE_URL` PostgreSQL
- `SECRET_KEY` strong production secret
- `METRICS_TOKEN` if metrics enabled

## Recommendation

Do not deploy directly to production yet.

Recommended next step:
1. Apply the small manual `main.py` router registration patch for `admin_queue_router`.
2. Run the full validation commands above.
3. Fix any Alembic head/down_revision conflict if the branch's current Alembic chain has newer heads than `20260410_0001`.
4. Open a PR and let GitHub Actions run.
5. After CI passes, deploy to staging first.

This branch is a strong first hardening patch, but because several deep OpenClaw/Webchat AI items were intentionally deferred to avoid unsafe broad edits, it should be treated as a staged hardening PR rather than the final production sign-off.
