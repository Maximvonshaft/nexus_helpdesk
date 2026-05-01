# NexusDesk Production Hardening Fix Report

Branch: `fix/production-hardening-webchat-openclaw-outbound`
Base: `main` at `99ecd42537e914df5200ce82f11f8812173c6fc4`

## Current status

This branch contains a staged production-hardening patch. It is ahead of `main` and focuses on Webchat public-entry hardening, manager capability reduction, OpenClaw CLI fallback safety, outbound dispatch recovery semantics, admin requeue endpoints, storage `persist_bytes` validation, CI production readiness, and lightweight regression tests.

## Completed fixes

### P0-1 Webchat CORS origin hardening

Files changed:
- `backend/app/settings.py`
- `backend/app/api/webchat.py`

Implemented:
- `WEBCHAT_ALLOWED_ORIGINS`
- `WEBCHAT_ALLOW_NO_ORIGIN`
- Public Webchat CORS allowlist validation.
- No production path intentionally returns `Access-Control-Allow-Origin: *`.
- Localhost remains allowed only for development/test/local.

Production requirement:
- Set `WEBCHAT_ALLOWED_ORIGINS` to the exact site origins allowed to embed the widget.

### P0-2 Webchat visitor token transport hardening

Files changed:
- `backend/app/api/webchat.py`
- `backend/app/static/webchat/widget.js`
- `backend/tests/test_production_hardening_static.py`

Implemented:
- Widget sends `X-Webchat-Visitor-Token` instead of putting the token in the URL.
- Backend prefers `X-Webchat-Visitor-Token`.
- Query token remains only as short compatibility path for old widgets.
- Static regression test verifies the widget does not actively emit the unsafe query fragment and contains the secure header name.

### P0-3 Webchat database-backed rate limiting

Files changed:
- `backend/app/settings.py`
- `backend/app/services/webchat_rate_limit.py`
- `backend/app/api/webchat.py`
- `backend/alembic/versions/20260427_prod_hardening.py`

Implemented:
- `WEBCHAT_RATE_LIMIT_BACKEND`, production default `database`.
- `WEBCHAT_RATE_LIMIT_WINDOW_SECONDS`.
- `WEBCHAT_RATE_LIMIT_MAX_REQUESTS`.
- `webchat_rate_limits` table.
- Public Webchat API invokes database-backed limiter before service handling.
- `X-Forwarded-For` is only trusted when the direct client host is in `TRUSTED_PROXY_IPS`.

Known issue to verify locally:
- `webchat_rate_limit.py` still uses a raw SQL datetime comparison. This should be validated under SQLite and PostgreSQL. A safer window-key rewrite was attempted but blocked by the execution environment safety filter.

### P0-4 Manager capability reduction

Files changed:
- `backend/app/services/permissions.py`
- `backend/tests/test_production_hardening_permissions.py`

Implemented:
- Removed default manager system-governance capabilities:
  - `user.manage`
  - `channel_account.manage`
  - `ai_config.manage`
  - `runtime.manage`
  - `market.manage`
- Admin still has full capability catalog.
- Capability overrides remain available for explicit exception grants.
- Tests cover manager capability removal, admin full capability, and OpenClaw CLI fallback setting behavior.

### P0-5 OpenClaw CLI fallback disabled by default

Files changed:
- `backend/app/settings.py`
- `backend/scripts/validate_production_readiness.py`
- `backend/tests/test_production_hardening_permissions.py`

Implemented:
- `OPENCLAW_CLI_FALLBACK_ENABLED` defaults to `false`.
- Production startup fails if fallback is enabled.
- Production readiness reports fallback-enabled environments as unsafe.

### P0-6 Outbound duplicate-send risk reduction

Files changed:
- `backend/app/services/message_dispatch.py`
- `backend/alembic/versions/20260427_prod_hardening.py`

Implemented:
- Stable local idempotency semantics using `nexusdesk-outbound-{message.id}`.
- Current implementation stores the local key in `provider_message_id` for compatibility.
- Idempotency key is included in outbound audit/route payloads.
- Already-sent messages are skipped before dispatch.
- `dispatch_pending_messages()` commits after every processed message.
- Added helper for dead outbound requeue.

Known follow-up:
- OpenClaw bridge/CLI/MCP provider-native idempotency still needs upstream provider contract support.

### P1-4 CI production readiness gate

Files changed:
- `.github/workflows/production-readiness.yml`
- `backend/scripts/validate_production_readiness.py`

Implemented:
- Added strict `production-readiness-gate` workflow.
- The workflow runs migrations against PostgreSQL and then runs production readiness with safe dummy production env values.
- Existing advisory checks are preserved.

### P1-5 Dead job/outbound requeue API implementation

Files changed:
- `backend/app/api/admin_queue.py`
- `backend/app/main.py`
- `backend/app/services/message_dispatch.py`

Implemented:
- Added runtime-managed endpoints:
  - `POST /api/admin/jobs/{job_id}/requeue`
  - `POST /api/admin/jobs/requeue-dead`
  - `POST /api/admin/outbound/{message_id}/requeue`
  - `POST /api/admin/outbound/requeue-dead`
- Added admin audit logging.
- Registered `admin_queue_router` in `backend/app/main.py`.

### P1-3 Storage-layer persist-bytes validation

Files changed:
- `backend/app/services/storage.py`

Implemented:
- `StorageBackend.persist_bytes()` now accepts optional:
  - `allowed_mime_types`
  - `allowed_extensions`
  - `max_bytes`
- Local and S3-compatible backends validate size, MIME type, and extension before persistence.

Still pending:
- `openclaw_bridge.persist_openclaw_attachment_reference()` must be wired to pass the OpenClaw attachment constraints into `persist_bytes()`.

## Partially completed / deferred items

### P1-1 OpenClaw auto-link account/market hardening

Status: deferred.

Reason:
- Requires careful edits to `openclaw_bridge.py`, route-aware matching tests, and local execution.
- Broad blind edits to this high-risk file were avoided without test execution.

Next PR should:
- Make `_find_matching_open_tickets()` accept full route context.
- Require channel/account/market disambiguation before auto-linking.
- Force unresolved quarantine when account context is absent in multi-account environments.

### P1-2 unresolved replay concurrency/idempotency hardening

Status: deferred.

Next PR should:
- Add replay state CAS or row lock.
- Block replay of `resolved`, `dropped`, or `replaying` events.
- Add/verify unique constraints for transcript and attachment references.

### P1-6 Webchat AI high-risk mode

Status: settings and readiness added, behavior not fully wired.

Implemented:
- `WEBCHAT_AI_AUTO_REPLY_MODE=off|safe_ack|safe_ai`
- Production default is `safe_ack`.
- Readiness checks warn if production is not `off` or `safe_ack`.

Still pending:
- `webchat_ai_service.py` must short-circuit bridge generation in `off` and `safe_ack` modes.
- High-risk intent fallback in `safe_ai` mode remains to be implemented.

## Migration

Added migration:
- `backend/alembic/versions/20260427_prod_hardening.py`
- Revision ID: `20260427_prod_hardening`
- Down revision: `20260410_0001`

Adds:
- `webchat_rate_limits`
- `ticket_outbound_messages.provider_idempotency_key`

Must verify locally:
- `cd backend && alembic heads`
- `cd backend && alembic upgrade head`

## Tests added

Added:
- `backend/tests/test_production_hardening_permissions.py`
- `backend/tests/test_production_hardening_static.py`

Covered:
- Manager no longer has high-risk system capabilities by default.
- Admin still has all capabilities.
- OpenClaw CLI fallback default is false.
- Production rejects OpenClaw CLI fallback enabled.
- Widget does not actively emit unsafe visitor-token query transport.
- Widget contains secure Webchat visitor-token header transport.

## Validation status

I could modify the GitHub branch through the GitHub connector, but I could not run the repository locally from this environment. Therefore the following remain required before merge:

```bash
python -m compileall backend/app backend/scripts
pytest -q backend/tests
cd backend && alembic heads
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
- PostgreSQL `DATABASE_URL`
- Strong `SECRET_KEY`
- `METRICS_TOKEN` if metrics are enabled

## Recommendation

Open a PR for this branch, but do not merge until CI and local validation pass.

Do not deploy directly to production yet.

Recommended path:
1. Run the full validation commands above.
2. Fix any Alembic head or import error if found.
3. Open PR from `fix/production-hardening-webchat-openclaw-outbound` into `main`.
4. Let GitHub Actions run.
5. Merge only after CI is green and a staging smoke test passes.
6. Create a follow-up PR for OpenClaw auto-link/replay and full Webchat AI safe mode wiring.
