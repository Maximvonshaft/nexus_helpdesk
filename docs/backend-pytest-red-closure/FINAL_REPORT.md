# Final Report: Backend Full Pytest Red Closure

## Metadata
- Base commit SHA: `d23a38d0e4345aa6839cf2274f2169ab03932114`
- Branch: `fix/backend-full-pytest-red-closure-v1`
- Reviewed implementation head SHA: `62d71b79d109e2b245065ed95ab4dd3ee5b422ca`
- Final branch head: use current GitHub PR metadata as source of truth because documentation-only closure commits update the branch head.

## Test Results
- Initial backend full result: `15 failed, 711 passed, 1 skipped`
- Final backend full result: `731 passed, 1 skipped, 424 warnings in 33.85s`
- Compile check: passed
- Migration head and upgrade checks: passed
- Migration drift gate: passed

## Warning Summary
- 424 warnings remain.
- Main classes: SQLAlchemy SQLite teardown warnings and FastAPI lifecycle deprecation warnings.
- Merge blocker: no.
- Follow-up: warning cleanup should be handled in a separate maintenance task.

## Changed Scope
- Migration test support: `backend/alembic/versions/20260520_0026_audit_reality_closure.py`
- Product contract alignment: `backend/app/api/webchat_fast.py`, `backend/app/services/webchat_fast_stream_service.py`
- Test updates: Codex, Fast Lane, stream replay, and stream feature-flag tests
- Evidence docs: `FAILURE_INVENTORY.md`, `FINAL_REPORT.md`, `pr_body.md`

## Root Cause Buckets
1. Async test marker mismatch.
2. SQLite migration downgrade and re-upgrade index handling.
3. WebChat fast stream and replay contract drift.
4. Stream settings test doubles missing newer fields.
5. Codex provider tests missing explicit feature flag setup.
6. Speedaf enqueue stub signature drift.

## Direct and Indirect Fixes
- Direct product alignment: WebChat fast stream ordering and settings compatibility.
- Direct migration support: safer expression-index detection and drop behavior in test migration cycles.
- Direct test updates: changed test files listed in the PR.
- Indirect fixes: some initial stream and Speedaf failures were resolved by shared product or test-infra changes rather than direct edits to each initially failing test file.

## PR #164 Hardening Preserved
PR #164 hardening is preserved. BackgroundJob active dedupe, admin action rate-limit atomic writes, and unresolved-event active idempotency remain intact.

## Risk
Risk is low-to-moderate. This PR is mostly test and contract closure, but it also includes minimal WebChat fast stream product-contract alignment. The updated contract is covered by tests.

## Release Status
Backend full pytest is green after this PR. This does not mean final release approval; external runtime validation remains required.
