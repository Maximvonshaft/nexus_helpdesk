# PATCH_MANIFEST

Patch name: `nexusdesk-main-audit-closure-patch`

Scope: source-overlay patch package for the current `main` branch layout. No `.git`, build artifacts, env secrets, uploads, logs, or databases are included.

## Modified / added files

| File | Type | Issue | Purpose |
|---|---|---|---|
| `backend/alembic/versions/20260421_governance_overlay_round4.py` | migration | P0-1 | Shortens Alembic revision to `20260421_gov_r4` while preserving migration semantics. |
| `webapp/src/lib/api.ts` | frontend code | P0-2 | Adds `VITE_API_BASE_URL` support while preserving same-origin default. |
| `webapp/.env.example` | frontend config docs | P0-2 | Documents local-to-cloud API base URL usage. |
| `backend/app/services/outbound_safety.py` | backend code | P0-3 | Adds deterministic outbound safety / fact-claim review gate. |
| `backend/app/services/message_dispatch.py` | backend code | P0-3, P1-3 | Enforces safety gate before dispatching queued outbound messages. |
| `backend/app/services/background_jobs.py` | backend code | P0-3, P1-3 | Saves AI auto-replies as review-required drafts instead of direct outbound send. |
| `backend/app/services/integration_auth.py` | backend code | P1-3 | Removes internal commit from integration authentication path; uses outer transaction boundary. |
| `.github/workflows/backend-ci.yml` | CI | P0-4 | Adds backend compile/test/import readiness gate. |
| `.github/workflows/postgres-migration.yml` | CI | P0-4 | Adds PostgreSQL Alembic migration gate. |
| `docs/deployment-runbook.md` | docs | P0-5, P1-5 | Documents service roles, update, rollback, and protected files. |
| `docs/migration-troubleshooting.md` | docs | P0-1, P0-5 | Documents Alembic revision length issue and remediation. |
| `backend/app/settings.py` | backend code | P1-1 | Adds `OPENCLAW_EXTRA_PATHS` parsing for MCP command lookup. |
| `backend/app/services/openclaw_mcp_client.py` | backend code | P1-1 | Removes hardcoded `/home/vboxuser/.local/bin`; uses configured command/path. |
| `backend/app/services/openclaw_runtime_service.py` | backend code | P1-1 | Surfaces extra path configuration as connectivity warning context. |
| `backend/scripts/run_openclaw_event_daemon.py` | backend script | P1-7 | Updates heartbeat to `error` on event daemon iteration failures. |
| `backend/app/services/observability.py` | backend code | P1-4 | Replaces queue snapshot Counter with Gauge and normalizes HTTP metric paths. |
| `backend/app/main.py` | backend code | P1-4 | Uses updated observability implementation and version label. |
| `deploy/.env.prod.example` | deploy config template | P1-5 | Adds production env template without real secrets. |
| `scripts/deploy/safe_update_server.sh` | deploy script | P1-5 | Adds non-destructive server update preflight and local config backup. |
| `scripts/deploy/rollback_release.sh` | deploy script | P1-5 | Adds explicit-confirm rollback helper for DB/image rollback. |
| `deploy/nginx/https.example.conf` | deploy config | P1-6 | Adds HTTPS reverse proxy template without replacing current Nginx config. |
| `docs/outbound-safety-architecture.md` | docs | P0-3, P2/P3 | Documents target Meta/Fact/Truth/Dispatcher architecture. |
| `docs/saas-roadmap.md` | docs | P2/P3 | Documents tenant/SaaS migration roadmap without risky table changes. |
| `docs/frontend-design-system.md` | docs | P2/P3 | Documents control-panel UI state and component standards. |
| `docs/backend-refactor-roadmap.md` | docs | P1-2, P2/P3 | Documents admin/models/transaction refactor sequence. |
| `docs/legacy-frontend-deprecation.md` | docs | P2/P3 | Documents legacy frontend deprecation policy. |
| `docs/frontend-workspace-refactor-plan.md` | docs | P1-8 | Documents safe Workspace component extraction plan. |
| `docs/backend-module-split-plan.md` | docs | P1-2 | Documents safe admin.py split plan without high-risk route rewrite. |
| `APPLY_PATCH.md` | delivery docs | all | Explains how to apply, validate, and roll back the patch. |
| `VERIFY_RESULTS.md` | delivery docs | all | Records verification performed in this sandbox and unverified items. |
| `PATCH_MANIFEST.md` | delivery docs | all | This manifest. |
