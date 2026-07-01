# Audit Reality Closure — Evidence

Audit timestamp: 2026-05-20T23:36:13+02:00

## Baseline

- Main commit SHA reviewed before changes: `494e2b3a91843f453916f17fb3683c6aee740811`
- Working branch: `fix/audit-reality-closure-v1`
- OS: `Linux 6.12.74+deb13+1-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.74-2 (2026-03-08) x86_64 GNU/Linux`
- Python: `3.13.5`
- Node: `v22.22.0`

## Code facts re-verified from the repo

### Frontend runtime/version facts

- `webapp/package.json` uses `react` `18.3.1` and `react-dom` `18.3.1`.
- `@types/react` is on `^19.2.0`, but that does **not** mean runtime React 19.
- `webapp/package.json` now exposes `npm run e2e` and includes `@playwright/test`.

Reference snippets:

```text
webapp/package.json
- "e2e": "playwright test"
- "react": "18.3.1"
- "react-dom": "18.3.1"
- "@playwright/test": "^1.55.0"
- "@types/react": "^19.2.0"
```

### Auth/token storage facts

`webapp/src/lib/api.ts` still stores the access token in `sessionStorage` and sends it via `Authorization: Bearer ...`.

Reference snippets:

```text
webapp/src/lib/api.ts
- return sessionStorage.getItem(STORAGE_KEY)
- sessionStorage.setItem(STORAGE_KEY, token)
- sessionStorage.removeItem(STORAGE_KEY)
- headers.set('Authorization', `Bearer ${token}`)
```

### Security-header facts

`backend/app/main.py` already sets both `Permissions-Policy` and `Content-Security-Policy`.
The current CSP still contains `style-src 'unsafe-inline'`.

Reference snippets:

```text
backend/app/main.py
- DEFAULT_CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; ..."
- response.headers['Permissions-Policy'] = _permissions_policy_for_request(...)
- response.headers['Content-Security-Policy'] = _content_security_policy_for_request(...)
```

## Repair evidence added on this branch

### A. BackgroundJob database-level active dedupe hardening

Problem confirmed:
- application-level dedupe existed in `enqueue_background_job()`
- no database-level active unique guard prevented race-created duplicates

Hardening added:
- partial unique index `uq_background_jobs_active_dedupe_key`
- model metadata updated to reflect the index
- `enqueue_background_job()` now keeps the existing pre-query, inserts inside `begin_nested()`, and on `IntegrityError` re-reads the active row instead of breaking the outer transaction

Reference snippets:

```text
backend/app/models.py
- uq_background_jobs_active_dedupe_key

backend/app/services/background_jobs.py
- with db.begin_nested():
- except IntegrityError:

backend/alembic/versions/20260520_0026_audit_reality_closure.py
- uq_background_jobs_active_dedupe_key
```

Validation:
- `backend/tests/test_background_job_dedupe_idempotency.py` passes
- coverage includes same-active-key single-row behavior, resolved/terminal row not blocking new active row, and outer transaction surviving `IntegrityError`

### B. Admin recovery/server-side rate limit hardening

Problem confirmed:
- sensitive requeue/replay/drop/consume-once admin actions had no backend-enforced per-user rate-limit bucket

Hardening added:
- new `admin_action_rate_limits` table
- new `AdminActionRateLimitBucket` model
- new `admin_action_rate_limit.py` service
- PostgreSQL/SQLite atomic bucket mutation via `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`
- endpoint wiring for:
  - background job requeue
  - background job requeue-dead
  - outbound requeue
  - outbound requeue-dead
  - unresolved-event replay
  - unresolved-event drop
  - ExternalChannel `consume-once`
- 429 responses include `request_id`
- rate-limit violations are logged/audited
- counting is isolated by user and by action key

Reference snippets:

```text
backend/app/api/admin.py
- enforce_admin_action_rate_limit(... action_key='external_channel.events.consume_once' ...)
- enforce_admin_action_rate_limit(... action_key='unresolved_event.replay' ...)
- enforce_admin_action_rate_limit(... action_key='unresolved_event.drop' ...)

backend/app/api/admin_queue.py
- enforce_admin_action_rate_limit(... action_key='background_job.requeue' ...)
- enforce_admin_action_rate_limit(... action_key='background_job.requeue_dead_batch' ...)
- enforce_admin_action_rate_limit(... action_key='outbound_message.requeue' ...)
- enforce_admin_action_rate_limit(... action_key='outbound_message.requeue_dead_batch' ...)
```

Validation:
- `backend/tests/test_admin_action_rate_limit.py` passes
- coverage proves:
  - first-hit concurrency does not 500
  - different users have independent counters
  - different action keys have independent counters
  - expired windows reset correctly
  - limited requests return `429`
  - responses include `request_id`
  - audit/log records exist

### C. ExternalChannel unresolved-event database-level active idempotency

Problem confirmed:
- unresolved events already used `payload_hash` at the application layer
- a database-level active uniqueness guard was still missing for concurrent persist/replay races

Hardening added:
- partial unique index `uq_external_channel_unresolved_active_payload_hash`
- key columns: `source`, `COALESCE(session_key, '')`, `payload_hash`
- active statuses guarded: `pending`, `failed`, `replaying`
- `persist_unresolved_external_channel_event_by_hash()` keeps the existing lookup, inserts inside `begin_nested()`, and on `IntegrityError` safely re-reads the existing active row
- resolved rows do not block a new active row
- `session_key=NULL` and `session_key=''` now intentionally collapse into the same active dedupe bucket so `NULL` can no longer bypass the DB uniqueness guard

Reference snippets:

```text
backend/app/models.py
- uq_external_channel_unresolved_active_payload_hash
- COALESCE(session_key, '')
- payload_hash IS NOT NULL AND status IN ('pending', 'failed', 'replaying')

backend/app/services/external_channel_unresolved_store.py
- current_payload_hash = compute_payload_hash(payload)
- with db.begin_nested():
- except IntegrityError:

backend/alembic/versions/20260520_0026_audit_reality_closure.py
- uq_external_channel_unresolved_active_payload_hash
- PARTITION BY source, COALESCE(session_key, ''), payload_hash
```

Validation:
- `backend/tests/test_external_channel_unresolved_idempotency.py` passes
- coverage proves:
  - same semantic payload key-order changes hash identically
  - same active payload/hash produces one active row
  - `session_key=None` and `session_key=''` cannot create parallel active rows
  - resolved rows do not block a new active row
  - `IntegrityError` recovery returns the existing row instead of surfacing a 500

### D. Frontend smoke/e2e closure

Problem confirmed:
- repo had no Playwright config or webapp e2e smoke suite

Hardening added:
- `webapp/playwright.config.ts`
- `webapp/e2e/smoke.spec.ts`
- mocked `/api/**` fixture flow using `sessionStorage` token setup; no real production account required
- `docs/e2e-smoke-runbook.md` updated with runnable commands and scope

Smoke coverage:
- login page renders
- unauthenticated protected route returns to `/login`
- agent/ordinary user navigation hides management entry points
- admin/capability user navigation shows management entry points

Validation:
- `cd webapp && npm run e2e` passed: `4 passed`

### E. Documentation fact correction

Outdated docs were corrected to stop describing the current webapp runtime as React 19.
Updated files include:
- `README.md`
- `docs/frontend-upgrade/README.md`
- `docs/frontend-upgrade/03-target-architecture-rfc.md`
- `docs/frontend-upgrade/11-engineering-handoff.md`
- `docs/frontend-upgrade/17-pr26-professional-review-report.md`

## Migration evidence

New migration:
- `backend/alembic/versions/20260520_0026_audit_reality_closure.py`

Behavior:
- creates `admin_action_rate_limits`
- normalizes duplicate active `background_jobs` before adding the active partial unique index
- normalizes duplicate active `external_channel_unresolved_events` before adding the active partial unique index
- downgrade removes the new indexes/table

Validation:
- `alembic heads` => `20260520_0026 (head)`
- `alembic upgrade head` passed
- migration contract tests passed after migration fix

## Important non-claims

This branch does **not** claim to have completed:
- HttpOnly cookie + CSRF migration
- removal of `style-src 'unsafe-inline'`
- legacy frontend deprecation/removal
- admin two-person approval / OTP
- full service transaction-boundary refactor
- external observability sink rollout
