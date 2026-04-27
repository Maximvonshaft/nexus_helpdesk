# NexusDesk Production Signoff Report

Final decision: **Not approved for production**

Branch: `fix/production-hardening-webchat-openclaw-outbound`
Merge base: `99ecd42537e914df5200ce82f11f8812173c6fc4`
Migration revision: `20260427_prod_hardening`

## Summary

This branch is a production-hardening candidate. It closes several P0 items around Webchat public ingress, manager permissions, OpenClaw CLI fallback defaults, outbound dispatch recovery semantics, admin requeue operations, and Webchat AI safe acknowledgement routing.

It is not a final production signoff because full runtime validation was not executed in this environment, and several OpenClaw deep-linking/replay protections still need a dedicated local-tested patch.

## Modified files

- `.github/workflows/production-readiness.yml`
- `PRODUCTION_HARDENING_FIX_REPORT.md`
- `PRODUCTION_SIGNOFF_REPORT.md`
- `backend/alembic/versions/20260427_prod_hardening.py`
- `backend/app/api/admin_queue.py`
- `backend/app/api/webchat.py`
- `backend/app/main.py`
- `backend/app/services/background_jobs.py`
- `backend/app/services/message_dispatch.py`
- `backend/app/services/permissions.py`
- `backend/app/services/storage.py`
- `backend/app/services/webchat_ai_safe_service.py`
- `backend/app/services/webchat_rate_limit.py`
- `backend/app/settings.py`
- `backend/app/static/webchat/widget.js`
- `backend/scripts/validate_production_readiness.py`
- `backend/tests/test_production_hardening_permissions.py`
- `backend/tests/test_production_hardening_static.py`

## Completed items

1. Webchat public CORS is allowlist-based through `WEBCHAT_ALLOWED_ORIGINS`.
2. Webchat visitor token is sent by `X-Webchat-Visitor-Token` in the widget.
3. Webchat API has a database-backed rate limit service and trusted-proxy handling.
4. Manager default system-governance capabilities are reduced.
5. OpenClaw CLI fallback defaults to disabled and is rejected in production.
6. Outbound dispatch has local idempotency semantics and per-message commits.
7. Admin queue requeue endpoints are implemented and registered.
8. Webchat AI jobs now route through `webchat_ai_safe_service.py`.
9. `off` mode skips external Webchat AI replies.
10. `safe_ack` mode sends deterministic acknowledgement without bridge/LLM.
11. `safe_ai` mode falls back to safe acknowledgement for high-risk intents.
12. Storage `persist_bytes()` supports MIME, extension, and max-size guards.
13. A production readiness GitHub Actions workflow was added.
14. Regression tests were added for manager permissions, CLI fallback defaults, and widget secure transport.

## Blocking items before production

1. Full local tests were not executed in this environment.
2. Alembic head and upgrade were not executed in this environment.
3. Frontend typecheck/build were not executed in this environment.
4. Docker build was not executed in this environment.
5. Staging smoke test was not executed in this environment.
6. OpenClaw auto-link strong account/channel/market matching is still deferred.
7. OpenClaw unresolved replay CAS/idempotency is still deferred.
8. OpenClaw attachment persistence still needs to pass the new storage validation parameters from `openclaw_bridge.py`.
9. Webchat database rate limiter should be verified on both SQLite and PostgreSQL.

## Required validation before merge

The following checks must pass in OpenClaw VBox, local Linux, or staging server:

- Python compile check for backend application and scripts.
- Full backend pytest suite.
- Alembic heads/current/upgrade validation.
- Webapp dependency install, typecheck, and build.
- Docker image build.
- Staging smoke test for health, auth, Webchat, outbound, OpenClaw sync, requeue, and attachment handling.

## Production configuration checklist

Required production settings include:

- `APP_ENV=production`
- PostgreSQL database URL
- Strong `SECRET_KEY`
- Console `ALLOWED_ORIGINS`
- Webchat `WEBCHAT_ALLOWED_ORIGINS`
- `WEBCHAT_RATE_LIMIT_BACKEND=database`
- `WEBCHAT_AI_AUTO_REPLY_MODE=safe_ack` or `off`
- `OPENCLAW_CLI_FALLBACK_ENABLED=false`
- OpenClaw bridge/MCP settings
- Outbound dispatch flag
- OpenClaw sync/event flags
- Storage backend and upload limits
- Metrics token if metrics are enabled
- Trusted proxy IPs if forwarding headers are used

## Rollback plan summary

Before deployment, record the current production commit, the new release commit, the database backup point, and the Docker rollback image tag.

Emergency stop switches:

- Set Webchat AI auto reply mode to off.
- Disable outbound dispatch.
- Disable OpenClaw sync.
- Disable OpenClaw event driver.
- Stop worker containers if queue behavior is unsafe.

## Final decision

Approved for PR: **Yes, as Draft PR**

Approved for merge: **No**

Approved for production: **No**

Final conclusion: **Not approved for production**
