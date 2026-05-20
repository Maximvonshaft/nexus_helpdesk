## Problem

The current `main` branch at `494e2b3a91843f453916f17fb3683c6aee740811` already had partial application-layer protections for BackgroundJob dedupe and OpenClaw unresolved-event idempotency, but it still lacked database-level race guards. Sensitive admin recovery actions also lacked backend-enforced per-user/action rate limiting. On the frontend side, docs were misstating the runtime as React 19 even though `webapp` actually runs React 18.3.1, and the repo had no minimal Playwright smoke suite.

## Evidence

- `webapp/package.json` runtime React is `18.3.1`; `@types/react` is `19.x`
- `webapp/src/lib/api.ts` uses `sessionStorage` + `Authorization: Bearer ...`
- `backend/app/main.py` already sets `Permissions-Policy` and CSP, but CSP still contains `style-src 'unsafe-inline'`
- `backend/app/services/background_jobs.py` and `backend/app/services/openclaw_unresolved_store.py` had application-layer dedupe/idempotency but no DB-level active partial unique guard
- follow-up review found two merge blockers in the first pass:
  - unresolved-event active uniqueness still allowed a `session_key IS NULL` bypass
  - admin rate-limit buckets still had a select-then-update race window under concurrency
- see `docs/audit-reality-closure/EVIDENCE.md`

## Changes

### Backend hardening
- added `admin_action_rate_limits` migration/model/service
- enforced server-side rate limiting for:
  - job requeue
  - dead-job batch requeue
  - outbound requeue
  - dead-outbound batch requeue
  - unresolved-event replay
  - unresolved-event drop
  - OpenClaw `consume-once`
- added `request_id` propagation support used by the rate-limit 429 path
- hardened admin rate-limit bucket writes to atomic `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`
- added DB-level active partial unique index for `background_jobs.dedupe_key`
- added DB-level active partial unique index for unresolved events on `(source, COALESCE(session_key, ''), payload_hash)`
- normalized duplicate active rows before index creation in migration
- normalized unresolved duplicates with `COALESCE(session_key, '')` grouping so `NULL` and empty string collapse into the same active bucket
- changed both insert paths to use nested transactions/savepoints and safe `IntegrityError` recovery instead of rolling back outer business work

### Frontend / docs
- corrected current-state React runtime references from React 19 to React 18.3.1
- added Playwright config and `npm run e2e`
- added mocked smoke coverage for login, unauth redirect, agent-hidden management nav, and admin-visible management nav
- updated `docs/e2e-smoke-runbook.md`
- added:
  - `docs/audit-reality-closure/EVIDENCE.md`
  - `docs/audit-reality-closure/FINAL_REPORT.md`

## Tests

### Targeted backend
- `pytest -q backend/tests/test_openclaw_unresolved_idempotency.py backend/tests/test_background_job_dedupe_idempotency.py backend/tests/test_admin_action_rate_limit.py`
  - `18 passed`

### Backend validation
- `cd backend && python -m compileall app scripts`
  - `python` not present in this environment
- `cd backend && python3 -m compileall app scripts`
  - passed
- `cd backend && pytest -q`
  - `15 failed, 711 passed, 1 skipped`
  - remaining failures are outside this PR scope and cluster around pre-existing async-plugin / Codex / WebChat stream / Speedaf test issues

### Migration
- `cd backend && alembic heads`
  - `20260520_0026 (head)`
- `cd backend && alembic upgrade head`
  - passed
- `cd backend && python scripts/check_model_migration_drift.py || true`
  - raw env/import-path issue with `python`
- `cd backend && PYTHONPATH=. python3 scripts/check_model_migration_drift.py || true`
  - script refused to run without PostgreSQL `DATABASE_URL`
- `cd backend && pytest -q tests/test_migration_drift_gate.py || true`
  - `3 passed`

### Frontend
- `cd webapp && npm ci`
  - passed
- `cd webapp && npm run typecheck`
  - passed
- `cd webapp && npm run build`
  - passed
- `cd webapp && npm run e2e`
  - `4 passed`

## Risk

- Low-to-moderate migration risk because this PR adds new indexes and a new rate-limit table.
- Duplicate-active-row normalization mutates pre-existing conflicting rows before unique index creation.
- Endpoint behavior changes now return `429` for repeated sensitive admin actions.
- This PR does **not** claim overall repo green status because unrelated backend test failures remain.

## Rollback

- revert this PR
- downgrade Alembic revision `20260520_0026`
- remove endpoint rate-limit enforcement and the new rate-limit service/model
- remove Playwright additions if frontend smoke needs to be rolled back independently

## Production Gate Status

- **Not production ready yet**
- still required before any production-ready claim:
  - staging smoke
  - OpenClaw Gateway gate
  - real-domain CORS validation
  - real bridge/MCP outbound validation
- still deferred to later PRs:
  - HttpOnly Cookie + CSRF
  - remove `style-src 'unsafe-inline'`
  - legacy frontend deprecation
  - admin two-person approval / OTP
  - full service transaction-boundary refactor
  - external observability sink
