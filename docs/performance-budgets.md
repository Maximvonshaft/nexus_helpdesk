# NexusDesk Runtime Performance Budgets

This document defines the release gate for PR 3 runtime, OpenClaw bridge, infrastructure, observability, and frontend performance closure.

## API budgets

- Core health/readiness endpoints should respond quickly under normal staging load.
- Admin list endpoints must use bounded pagination or explicit limits.
- Long-poll / polling endpoints must have bounded wait time and must not create unbounded write amplification.
- Database query timing instrumentation records low-cardinality SQL categories only. SQL parameters and customer content must never be logged or used as labels.

## WebChat budgets

- Public message polling must use throttled `last_seen_at` writes.
- WebChat events polling must use bounded `wait_ms`, stable `after_id`, `limit + 1`, and `has_more` semantics.
- Event write paths that are not the source of truth should be best-effort and must not break primary ticket/conversation state transitions.

## OpenClaw bridge budgets

- Remote bridge calls use a pooled HTTP client.
- Bridge timeout and connection limits are explicit and environment-tunable.
- Timeout, invalid JSON, HTTP, and transport failures degrade safely and return bounded error codes.
- Bridge logs must scrub token, secret, password, and API-key-like values.
- Remote-gateway mode with CLI fallback disabled must not silently start a local subprocess fallback.

## OpenClaw unresolved event idempotency

- Active unresolved-event dedupe uses canonical `payload_hash`, not `payload_json` text equality.
- `payload_json` remains stored for replay/debug and is not removed.
- Same semantic JSON with different key order must dedupe into one active unresolved row.
- Resolved historical rows must not block a new pending row.

## Worker / daemon budgets

- Worker and OpenClaw daemon readiness probes are read-only.
- Probe scripts must reject destructive arguments such as restart, down, rm, kill, prune, delete, truncate, or drop.
- Worker metrics track job duration, wait time, retry count, and oldest pending age with low-cardinality labels.

## Frontend bundle budgets

Default CI budgets:

- Largest single JavaScript chunk gzip: 180 KB.
- First-screen JavaScript gzip: 300 KB.

`npm run size-report` enforces these budgets after `npm run build`.

## Staging verification plan

1. Build the server image from the clean branch.
2. Run `docker compose -f deploy/docker-compose.server.yml config`.
3. Run `bash -n scripts/smoke/runtime_performance_baseline.sh`.
4. Run `python scripts/smoke/worker_daemon_readiness_probe.py --help`.
5. Deploy to staging only after all CI workflows are green.
6. Verify `/healthz`, `/readyz`, `/metrics`, worker container health, and OpenClaw daemon health in staging.

## Rollback plan

- Prefer code/image rollback first.
- Runtime rollback: revert this PR or roll back the image tag. Restore the previous Uvicorn command only as emergency runtime rollback.
- Nginx rollback: revert `deploy/nginx/default.conf` if routing, cache, or header regressions appear.
- Database rollback: prefer code rollback first. If required on disposable/non-production DBs, `alembic downgrade -1` removes only `payload_hash` index/column and does not remove `payload_json`.
- Frontend rollback: revert Vite chunking and API timeout/request-id changes independently if route loading or API behavior regresses.

## Safety boundaries

- No production database access.
- No production `.env` mutation.
- No production restart.
- No production load or pressure testing.
- No token, secret, cookie, or customer PII exposure in logs, metrics, or artifacts.
