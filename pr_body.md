## Problem
Issue #165 required closing the remaining backend full pytest failures after PR #164 was merged.

## Baseline
- PR #164 merge commit: d23a38d0e4345aa6839cf2274f2169ab03932114
- initial backend full pytest: 15 failed, 711 passed, 1 skipped

## Changes
- **async/plugin/test infra**: Added correct async testing configurations/markers to support `async def` test runners.
- **Codex**: Fixed environment state leakage across tests that broke rigorous configuration assertions.
- **WebChat stream**: Realigned tests that lacked mock requirements like `app_env` or `is_openclaw_stream_configured`.
- **Speedaf**: Updated test stub signature (`fake_enqueue()`) to match the new background job payload expectations.
- **migration/model drift**: Fixed SQLite Alembic downgrade script logic where conditional index dropping caused re-upgrade failures.
- **replay/assertion contract drift**: Synchronized stream expectations so tests accurately assert the updated `reply_delta` vs `final` and `replay` output event shapes.
- **actual product regression, if any**: None. The failures were isolated to test assertions, environment variables, test stubs, and migration downgrade scripts used heavily during tests.

## Tests
- `python3 -m compileall app scripts` ✅
- `pytest -q` ✅ 731 passed, 1 skipped, 424 warnings in 33.85s
- `alembic heads` ✅
- `alembic upgrade head` ✅
- `pytest -q tests/test_migration_drift_gate.py` ✅

## Risk
This PR only modified test infrastructure, contract assertions, test environment isolation, and SQLite-specific migration downgrade limits. It does **not** touch or weaken production logic, retaining all PR #164 hardening.

## Rollback
`git revert -m 1 <merge_commit_sha>` to cleanly roll back these test modifications.

## Production Gate Status
This PR makes backend full pytest green, but does not make NexusDesk production ready.

Remaining gates:
- staging smoke
- OpenClaw Gateway gate
- real-domain CORS validation
- real bridge/MCP outbound validation
- warning cleanup follow-up if needed