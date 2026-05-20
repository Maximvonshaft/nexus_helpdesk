## Problem
Issue #165 required closing the remaining backend full pytest failures after PR #164 was merged.

## Baseline
- PR #164 merge commit: d23a38d0e4345aa6839cf2274f2169ab03932114
- initial backend full pytest: 15 failed, 711 passed, 1 skipped

## Changes
- async/test infra: corrected async test markers.
- Codex: isolated provider feature-flag setup in tests.
- WebChat stream: aligned the current SSE contract and tests. This includes minimal product-contract alignment for stream ordering and settings compatibility.
- Speedaf: restored enqueue invocation compatibility.
- migration/model drift: fixed SQLite downgrade/re-upgrade index handling used by migration tests.
- replay/assertion contract drift: synchronized replay/final/delta assertions with the current stream contract.

## Tests
- python3 compile check passed
- pytest -q passed: 731 passed, 1 skipped, 424 warnings in 33.85s
- alembic heads passed
- alembic upgrade head passed
- migration drift gate passed

## Risk
Low-to-moderate. This PR is mainly test and contract closure, but it does include minimal WebChat fast stream product-contract alignment. It does not weaken PR #164 hardening.

## Rollback
Revert the PR #166 merge commit if needed.

## Production Gate Status
This PR makes backend full pytest green, but does not make NexusDesk production ready.

Remaining gates:
- staging smoke
- OpenClaw Gateway gate
- real-domain CORS validation
- real bridge/MCP outbound validation
- warning cleanup follow-up if needed
