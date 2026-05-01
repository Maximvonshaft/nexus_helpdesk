# NexusDesk main governance hardening patch notes

## Scope

This patch hardens governance boundaries on `main` without broad product rewrites. It focuses on:

1. Frontend capability alignment
2. Persona / knowledge governance read protection
3. Webchat rate-limit and visitor-token hardening
4. CI readiness enforcement
5. Server deployment drift prevention
6. Webchat static route formalization

## Modified files

### Backend authorization

- `backend/app/services/permissions.py`
  - Added `ai_config.read` capability.
  - Added `ensure_can_read_ai_configs()`.
  - Preserved `ai_config.manage` as an implicit read-capable permission.

- `backend/app/api/persona_profiles.py`
  - Added `ensure_can_read_ai_configs()` to governance read endpoints.
  - List, detail, and resolve-preview now require `ai_config.read` or `ai_config.manage`.

- `backend/app/api/knowledge_items.py`
  - Added `ensure_can_read_ai_configs()` to governance read/search endpoints.
  - List, detail, and search-published now require `ai_config.read` or `ai_config.manage`.

### Frontend authorization

- `webapp/src/lib/access.ts`
  - Removed role-derived governance access for user, channel, runtime, AI config, and market management.
  - Governance visibility now depends on backend-provided `capabilities`.
  - Role is kept only for display-oriented workspace hint text.

### Webchat security and rate limiting

- `backend/app/api/webchat.py`
  - Header token is now the default safe visitor-token transport.
  - Query/body token compatibility is opt-in only through `WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=true`.
  - Webchat init resumes conversations by reading `X-Webchat-Visitor-Token` when present.

- `backend/app/settings.py`
  - Added `webchat_allow_legacy_token_transport`.
  - Production refuses to start if `WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=true`.

- `backend/app/services/webchat_service.py`
  - Removed duplicate in-memory `_RATE_BUCKETS` and `_enforce_rate_limit` logic.
  - Webchat throttling is now centralized in `backend/app/services/webchat_rate_limit.py`.

- `backend/app/static/webchat/widget.js`
  - Resume token is sent through `X-Webchat-Visitor-Token` header.
  - Init request body no longer carries `visitor_token`.

### Static routing

- `backend/app/main.py`
  - Removed the ad-hoc `NEXUSDESK ROUND B WEBCHAT STATIC HOTFIX` middleware.
  - Added formal `app.mount('/static/webchat', StaticFiles(...))` before SPA fallback.
  - Preserves access to `/static/webchat/widget.js` and `/static/webchat/demo.html`.

### CI / readiness

- `.github/workflows/backend-ci.yml`
  - Removed non-blocking `|| true` readiness behavior.
  - Added strict production-like readiness profile.

- `.github/workflows/postgres-migration.yml`
  - Removed non-blocking `|| true` readiness behavior.
  - Keeps Alembic PostgreSQL migration check and then runs strict readiness.

- `backend/scripts/validate_production_readiness.py`
  - Reports `webchat_allow_legacy_token_transport`.
  - Warns if legacy token transport is enabled.

### Deployment governance

- `deploy/docker-compose.server.example.yml`
  - Added reproducible single-server/VM deployment template.
  - Includes `postgres`, `app`, `worker`, `sync-daemon`, and `event-daemon`.
  - Keeps runtime data under `../data` and avoids committing secrets.

- `deploy/.env.prod.example`
  - Expanded production environment template.
  - Documents PostgreSQL, S3, OpenClaw, Webchat, metrics, and attachment-fetch safety settings.
  - Explicitly sets `WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=false`.

- `README.md`
  - Added server deployment drift prevention section.
  - Documents that live `deploy/.env.prod`, `data/`, uploads, and server-only overrides must not be destroyed by `git reset --hard`.

### Regression tests

- `backend/tests/test_round27_frontend_hardening.py`
  - Added static invariants for frontend capability-only governance access.
  - Added checks for persona/knowledge governance read guards.
  - Added checks for Webchat token transport and duplicate rate-limit removal.
  - Added checks for CI blocking readiness behavior.
  - Added checks for server deployment templates and README drift-prevention notes.

## Risk assessment

### Low risk

- Frontend permission functions now align more closely with backend authorization.
- Webchat duplicate service-level throttling removal reduces inconsistent multi-worker behavior.
- Static webchat route is now explicit rather than middleware-based.

### Medium risk

- Users with `manager` role but without explicit governance capabilities may stop seeing admin/control-plane navigation items. This is intended. Grant the needed capability through the existing capability override workflow.
- Persona and knowledge governance read endpoints now return `403` unless the user has `ai_config.read` or `ai_config.manage`. This is intended. If customer-service agents need safe published knowledge, expose a separate published-only lookup endpoint with reduced fields.
- Webchat clients relying on query/body visitor token transport need to be updated. The bundled widget now uses header transport.

## Validation commands

Run locally or in CI:

```bash
python -m compileall backend/app backend/scripts
pytest -q \
  backend/tests/test_outbound_safety.py \
  backend/tests/test_next_phase_max_push.py \
  backend/tests/test_openclaw_local_ops.py \
  backend/tests/test_round20a_rectification.py \
  backend/tests/test_round20b_legacy_frontend.py \
  backend/tests/test_round27_frontend_hardening.py
cd webapp && npm ci && npm run typecheck && npm run build
cd backend && alembic upgrade head
cd backend && APP_ENV=development DATABASE_URL=postgresql+psycopg://helpdesk:helpdesk@127.0.0.1:5432/helpdesk STORAGE_BACKEND=s3 OPENCLAW_TRANSPORT=mcp OPENCLAW_CLI_FALLBACK_ENABLED=false WEBCHAT_RATE_LIMIT_BACKEND=database WEBCHAT_AI_AUTO_REPLY_MODE=safe_ack WEBCHAT_ALLOWED_ORIGINS=https://example.test python scripts/validate_production_readiness.py
```

## Rollback guidance

Prefer reverting the patch commit range rather than manually editing live files. If a hot rollback is needed:

1. Revert the commits touching `webapp/src/lib/access.ts` only if the frontend accidentally hides required admin navigation.
2. Revert the commits touching `persona_profiles.py`, `knowledge_items.py`, and `permissions.py` only if a required governance-reader account was not granted `ai_config.read`.
3. Do not re-enable `WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT` in production except as a short emergency compatibility bridge.
4. Do not remove the explicit `/static/webchat` mount unless the SPA fallback is also changed to protect those assets.

## Deployment recommendation

Do not deploy blindly to the current server until CI has passed and the live server-specific files are backed up:

- `deploy/.env.prod`
- `deploy/docker-compose.server.yml`
- `data/`
- upload/storage directories
- reverse-proxy files

After backup, deploy through image rebuild, migration, restart, and health checks.
