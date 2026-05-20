# Audit Reality Closure — Final Report

## 1. Scope identity

- Current main commit SHA reviewed as baseline: `494e2b3a91843f453916f17fb3683c6aee740811`
- Working branch: `fix/audit-reality-closure-v1`
- Audit/repair timestamp: `2026-05-20T23:36:13+02:00`

## 2. Modified files in this round

### Backend
- `backend/alembic/versions/20260520_0026_audit_reality_closure.py`
- `backend/app/api/admin.py`
- `backend/app/api/admin_queue.py`
- `backend/app/db.py`
- `backend/app/models.py`
- `backend/app/services/admin_action_rate_limit.py`
- `backend/app/services/background_jobs.py`
- `backend/app/services/openclaw_unresolved_store.py`
- `backend/app/settings.py`
- `backend/tests/test_admin_action_rate_limit.py`
- `backend/tests/test_background_job_dedupe_idempotency.py`
- `backend/tests/test_openclaw_unresolved_idempotency.py`

### Frontend / docs
- `webapp/package.json`
- `webapp/package-lock.json`
- `webapp/playwright.config.ts`
- `webapp/e2e/smoke.spec.ts`
- `README.md`
- `docs/e2e-smoke-runbook.md`
- `docs/frontend-upgrade/README.md`
- `docs/frontend-upgrade/03-target-architecture-rfc.md`
- `docs/frontend-upgrade/11-engineering-handoff.md`
- `docs/frontend-upgrade/17-pr26-professional-review-report.md`
- `docs/audit-reality-closure/EVIDENCE.md`
- `docs/audit-reality-closure/FINAL_REPORT.md`

## 3. Re-judgment of the original audit claims

### 3.1 BackgroundJob dedupe gap
- Judgment: **部分准确**
- Why:
  - The code already had application-layer dedupe by `dedupe_key`.
  - The missing part was the database-level active unique guard for race conditions.
- Closure status: **已修复**

### 3.2 Admin recovery high-risk actions lacked backend throttling
- Judgment: **准确**
- Why:
  - Sensitive admin replay/requeue/drop/consume-once flows did not have server-enforced per-user action buckets.
- Closure status: **已修复**

### 3.3 OpenClaw unresolved event idempotency gap
- Judgment: **部分准确**
- Why:
  - Payload-hash application-layer idempotency already existed.
  - The missing part was the database-level active uniqueness guard and safe savepoint-based recovery on insert race.
- Closure status: **已修复**

### 3.4 Frontend React version claim
- Judgment: **错误/过时**
- Why:
  - Current runtime React is `18.3.1`, not React 19.
  - `@types/react` 19.x had been misread as runtime React 19.
- Closure status: **已修复**

### 3.5 Frontend smoke/e2e gap
- Judgment: **准确**
- Why:
  - The repo had no Playwright config or webapp smoke suite.
- Closure status: **已修复**

## 4. Completed fixes

### 4.1 BackgroundJob dedupe DB-level hardening

Implemented:
- partial unique index: `uq_background_jobs_active_dedupe_key`
- duplicate-active-row normalization in migration before index creation
- `enqueue_background_job()` now uses `begin_nested()` and recovers from `IntegrityError` by re-reading the active row
- outer transaction is preserved

Rollback:
- downgrade the migration to remove the partial unique index
- revert `background_jobs.py` savepoint recovery logic

### 4.2 Admin recovery rate limit

Implemented:
- new `admin_action_rate_limits` table and model
- new server-side enforcement service
- backend wiring across:
  - job requeue
  - dead-job batch requeue
  - outbound requeue
  - dead-outbound batch requeue
  - unresolved-event replay
  - unresolved-event drop
  - OpenClaw `consume-once`
- 429 responses include `request_id`
- audit/log trail exists
- counters are isolated by user and by action key

Rollback:
- downgrade migration to drop `admin_action_rate_limits`
- revert endpoint wiring and service import/use

### 4.3 OpenClaw unresolved event DB-level idempotency

Implemented:
- partial unique index: `uq_openclaw_unresolved_active_payload_hash`
- guarded keys: `source`, `session_key`, `payload_hash`
- guarded statuses: `pending`, `failed`, `replaying`
- duplicate-active-row normalization before index creation
- `persist_unresolved_openclaw_event_by_hash()` now uses savepoint recovery and returns the surviving active row on `IntegrityError`
- resolved rows remain able to admit a new active row

Rollback:
- downgrade the migration to remove the partial unique index
- revert savepoint recovery logic in unresolved-event persistence

### 4.4 Playwright e2e smoke

Implemented:
- `npm run e2e`
- Playwright config using local preview server
- mock `/api/**` fixture-based smoke, no real production account dependency
- smoke coverage for login, unauth redirect, agent-hidden admin nav, admin-visible management nav

Rollback:
- revert `webapp/playwright.config.ts`, `webapp/e2e/`, and `package.json` script/dependency additions

### 4.5 Documentation fact correction

Implemented:
- corrected React runtime references from React 19 to React 18.3.1 where they were claiming current-state fact
- added e2e local run instructions
- added evidence/final-report artifacts for this audit-closure round

Rollback:
- revert the doc updates

## 5. Validation summary

## Backend targeted validation
- `pytest -q backend/tests/test_openclaw_unresolved_idempotency.py backend/tests/test_background_job_dedupe_idempotency.py backend/tests/test_admin_action_rate_limit.py`
- Result: **13 passed**

## Required backend commands
- `cd backend && python -m compileall app scripts`
  - Result in this environment: `python` command missing
- `cd backend && python3 -m compileall app scripts`
  - Result: **passed**
- `cd backend && pytest -q`
  - Result: **15 failed, 711 passed, 1 skipped**
  - Remaining failures are outside this audit-closure scope and cluster around:
    - missing async pytest plugin / `pytest.mark.asyncio` handling in `test_codex_upstream_reply_transport.py`
    - pre-existing Codex app-server production guard tests
    - pre-existing WebChat stream feature/replay/final-parse contract failures
    - pre-existing Speedaf enqueue test monkeypatch signature mismatch

## Required migration commands
- `cd backend && alembic heads`
  - Result: `20260520_0026 (head)`
- `cd backend && alembic upgrade head`
  - Result: **passed**
- `cd backend && python scripts/check_model_migration_drift.py || true`
  - Raw result in this environment: failed because `python` command/import path assumption did not resolve `app`
- `cd backend && PYTHONPATH=. python3 scripts/check_model_migration_drift.py || true`
  - Result: script refused to run because it requires PostgreSQL `DATABASE_URL`
- `cd backend && pytest -q tests/test_migration_drift_gate.py || true`
  - Result: **3 passed**

## Required frontend commands
- `cd webapp && npm ci`
  - Result: **passed**
- `cd webapp && npm run typecheck`
  - Result: **passed**
- `cd webapp && npm run build`
  - Result: **passed**
- `cd webapp && npm run e2e`
  - Result: **4 passed**

## Global search verification
- no incorrect current-state React 19 claim remains in the new audit-closure docs; remaining `React 19` hits are explanatory references to the outdated wording
- no new bad `Permissions-Policy`-missing-policy claim found in the new audit docs
- admin requeue/replay/drop/consume-once endpoints are wired to `enforce_admin_action_rate_limit()`
- background job DB-level active unique guard is present in model + migration + runtime logic
- unresolved-event DB-level active unique guard is present in model + migration + runtime logic

## 6. Deferred items kept for later PRs

Not fixed in this round:
- HttpOnly Cookie + CSRF
- remove `style-src 'unsafe-inline'`
- legacy frontend deprecation
- admin two-person approval / OTP
- full service transaction boundary refactor
- external observability sink

## 7. Production deployment conclusion

Conclusion:
- **本轮完成后仍不能直接宣称 production ready。**

Required gates still outstanding before any production-ready claim:
- staging smoke
- OpenClaw Gateway gate
- real-domain CORS validation
- real bridge/MCP outbound validation

Additional caution:
- the repository-wide backend suite is still not globally green in this environment (`15` remaining failures outside this closure scope), so this branch closes the targeted audit gaps but does not close overall production-readiness risk.

## 8. Deliverables produced in this round

- `docs/audit-reality-closure/EVIDENCE.md`
- `docs/audit-reality-closure/FINAL_REPORT.md`
