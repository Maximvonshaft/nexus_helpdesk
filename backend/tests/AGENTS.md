# backend/tests/AGENTS.md — Backend Test Execution Contract

This contract applies to `backend/tests/**`. Tests are production evidence, not decoration. Do not weaken or delete tests to make a PR pass.

## 1. Test selection rule

When code changes, select tests by impacted behavior, not by filename convenience.

```text
API route changed       -> route tests + service tests + auth/permission tests
service changed         -> service tests + calling API tests
model/migration changed -> migration tests + model users + API/service tests
WebChat changed         -> WebChat fast/event/security/provider tests
WebCall changed         -> voice/WebCall production tests + PR guard tests
Provider/Codex changed  -> provider runtime + Codex + WebChat provider tests
OpenClaw changed        -> bridge/sync/unresolved/worker/runtime-health tests
Speedaf changed         -> Speedaf action + background job + audit/idempotency tests
Storage/files changed   -> file visibility + MIME/size/storage readiness tests
```

## 2. Baseline commands

Backend compile:

```bash
set -Eeuo pipefail
PYTHONPATH=backend python -m compileall backend/app backend/scripts
```

Targeted tests:

```bash
PYTHONPATH=backend pytest -q <targeted tests>
```

Full backend suite:

```bash
PYTHONPATH=backend pytest -q backend/tests
```

## 3. CI group awareness

The backend CI currently groups tests by behavior. Keep this structure in mind when adding tests:

```text
outbound safety and message semantics
production settings contracts
OpenClaw local ops and bridge resilience
worker/daemon readiness
observability and metrics
WebChat voice static/API/foundation
WebChat event, polling, RBAC, throttling, query count
admin/lite pagination
ticket search/timeline/detail summary
operator queue and replay/audit
strict readiness configuration
```

Add new tests near the closest behavior group. Do not create vague catch-all tests that hide which contract is protected.

## 4. Test quality requirements

Good tests must state a production contract:

```text
security gate holds
rate limit holds
idempotency holds
fallback is safe
audit/event row is written
queue job is deduped or retryable
response shape remains compatible
PII is redacted
feature flag defaults safe/off
demo route is not production route
```

Weak tests to avoid:

```text
only import smoke without asserting behavior
asserting implementation detail without business contract
mocking away the exact boundary being tested
duplicating tests without new coverage
snapshot-only tests for critical API behavior
```

## 5. Fixtures and state

- Keep tests isolated.
- Do not require real external OpenClaw, Codex, Speedaf, LiveKit, S3, or production database unless explicitly marked as integration/staging.
- Use fakes/mocks only at the external boundary; keep NexusDesk policy/permission/idempotency logic real.
- Do not put secrets or real tokens in fixtures.
- Avoid time-sensitive flakes; use deterministic time helpers if available.

## 6. When fixing a bug

A fix should normally add or update a regression test that fails before the fix and passes after the fix.

For production bugs, test at the highest useful contract level:

```text
public route -> API test
operator workflow -> API + webapp contract/e2e if possible
provider fallback -> service/router test
migration/data issue -> migration test
worker/daemon issue -> job/daemon readiness test
```

## 7. Reporting skipped tests

If a test cannot run locally, report:

```text
exact command
exact failure/blocker
whether CI can run it
risk of not running it locally
next verification command
```

Do not report `validated` when the actual result is `skipped`.
