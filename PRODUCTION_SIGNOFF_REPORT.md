# NexusDesk Production Signoff Report

Final decision: **Approved for merge candidate, not yet approved for direct production deployment**

Branch: `fix/production-hardening-webchat-openclaw-outbound`

Latest commit: `ff929b83b20034bbcdbe874c85ed17e9d2d7edd3`

Migration revision: `20260427_prod_hardening`

PR: https://github.com/Maximvonshaft/nexus_helpdesk/pull/16

## Validation result

### Backend

Passed:

- Focused regression tests: **5 passed**
- Full backend pytest: **121 passed**
- Python compileall: **passed**
- PostgreSQL Alembic heads: **single head**
- PostgreSQL Alembic upgrade head: **passed**

Alembic current head:

```text
20260427_prod_hardening
```

### Frontend

Passed:

- `npm ci`
- `npm run typecheck`
- `npm run build`

Build output:

```text
vite build completed successfully
196 modules transformed
frontend_dist generated
```

Known warning:

```text
npm audit reported 1 moderate severity vulnerability
```

This should be reviewed before final production deployment, but it did not block the build.

### Docker

Passed:

```text
docker build -t nexusdesk/helpdesk:hardening-check .
```

Image built:

```text
nexusdesk/helpdesk:hardening-check
```

## Completed hardening items

1. Webchat public CORS origin allowlist.
2. Webchat visitor token moved to `X-Webchat-Visitor-Token`.
3. Webchat database-backed rate limiting.
4. Manager default high-risk system permissions removed.
5. OpenClaw CLI fallback disabled by default.
6. Production rejects OpenClaw CLI fallback enabled.
7. Outbound local idempotency semantics.
8. Outbound per-message commit behavior.
9. Dead job and dead outbound requeue endpoints.
10. Admin queue router registered.
11. Webchat AI safe wrapper service added.
12. `WEBCHAT_AI_AUTO_REPLY_MODE=off` skips external auto reply.
13. `safe_ack` mode sends deterministic acknowledgement without bridge/LLM.
14. `safe_ai` mode falls back to safe acknowledgement for high-risk intents.
15. Storage `persist_bytes()` supports MIME, extension, and max-size guards.
16. OpenClaw MCP event handling now reuses the active MCP client in the tested path.
17. Alembic migration chain fixed to a single production head.
18. Source release packaging test passes.
19. Production readiness workflow added.

## Remaining production blockers

The branch is now suitable as a **merge candidate**, but not a direct production deployment candidate until staging smoke is completed.

Remaining blockers before production deployment:

1. Real staging smoke test not yet executed against a running NexusDesk service.
2. Webchat public origin behavior must be tested against the real customer/staging domain.
3. Outbound dispatch must be tested with the real OpenClaw bridge/MCP environment.
4. OpenClaw unresolved replay concurrency should still receive a deeper dedicated hardening PR.
5. OpenClaw attachment persistence should be checked end-to-end with real attachment payloads.
6. The npm moderate vulnerability should be reviewed with `npm audit`.

## Required production environment variables

Minimum required production settings:

```env
APP_ENV=production
DATABASE_URL=postgresql+psycopg://...
SECRET_KEY=...
ALLOWED_ORIGINS=https://...
WEBCHAT_ALLOWED_ORIGINS=https://...
WEBCHAT_RATE_LIMIT_BACKEND=database
WEBCHAT_AI_AUTO_REPLY_MODE=safe_ack
OPENCLAW_CLI_FALLBACK_ENABLED=false
OPENCLAW_BRIDGE_ENABLED=true
OPENCLAW_TRANSPORT=mcp
ENABLE_OUTBOUND_DISPATCH=true
OPENCLAW_SYNC_ENABLED=true
OPENCLAW_EVENT_DRIVER_ENABLED=true
STORAGE_BACKEND=local or s3
MAX_UPLOAD_BYTES=10485760
OPENCLAW_ATTACHMENT_MAX_DOWNLOAD_BYTES=10485760
METRICS_ENABLED=true
METRICS_TOKEN=...
TRUSTED_PROXY_IPS=...
```

## Rollback switches

Emergency production stop switches:

```env
WEBCHAT_AI_AUTO_REPLY_MODE=off
ENABLE_OUTBOUND_DISPATCH=false
OPENCLAW_SYNC_ENABLED=false
OPENCLAW_EVENT_DRIVER_ENABLED=false
```

## Final decision

Approved for PR: **Yes**

Approved to leave Draft state after CI is green: **Yes**

Approved for merge candidate: **Yes**

Approved for direct production deployment: **No, staging smoke still required**

Final conclusion:

**Approved for merge candidate / Not approved for direct production deployment**
