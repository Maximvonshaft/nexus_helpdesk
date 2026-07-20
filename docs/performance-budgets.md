# NexusDesk Runtime Performance Budgets

This document defines the production runtime, infrastructure, observability, worker, provider-adapter, and frontend performance budgets.

## API budgets

- Core health/readiness endpoints should respond quickly under normal staging load.
- Admin list endpoints must use bounded pagination or explicit limits.
- Long-poll / polling endpoints must have bounded wait time and must not create unbounded write amplification.
- Database query timing instrumentation records low-cardinality SQL categories only. SQL parameters and customer content must never be logged or used as labels.

## WebChat budgets

- Public message polling must use throttled `last_seen_at` writes.
- WebChat events polling must use bounded `wait_ms`, stable `after_id`, `limit + 1`, and `has_more` semantics.
- Event write paths that are not the source of truth should be best-effort and must not break primary ticket/conversation state transitions.

## Provider adapter budgets

- Provider adapters use pooled clients where applicable.
- Timeouts and connection limits are explicit and environment-tunable.
- Timeout, invalid response, HTTP, and transport failures degrade safely with bounded error codes.
- Adapter logs scrub tokens, secrets, passwords, API keys, and customer content.
- Disabled adapters must never silently activate a subprocess or alternate provider path.

## Worker / daemon budgets

- Worker readiness probes are read-only and must prove durable progress freshness.
- Probe scripts must reject destructive arguments such as restart, down, rm, kill, prune, delete, truncate, or drop.
- Worker metrics track job duration, wait time, retry count, and oldest pending age with low-cardinality labels.

## Frontend bundle budgets

Default CI budgets:

- Largest single JavaScript chunk gzip: 180 KB.
- First-screen JavaScript gzip: 300 KB.

`npm run size-report` enforces these budgets after `npm run build`.

## Staging verification plan

1. Build the server image from the clean branch.
2. Run `docker compose -f deploy/docker-compose.controlled.yml config`.
3. Run `bash -n scripts/smoke/runtime_performance_baseline.sh`.
4. Run `python scripts/smoke/worker_daemon_readiness_probe.py --help`.
5. Deploy to staging only after all CI workflows are green.
6. Verify `/healthz`, `/readyz`, `/metrics`, worker progress freshness, and provider adapter health in staging.

## Rollback plan

- Prefer code/image rollback first.
- Runtime rollback: revert this PR or roll back the image tag. Restore the previous Uvicorn command only as emergency runtime rollback.
- Nginx rollback: revert `deploy/nginx/default.conf` if routing, cache, or header regressions appear.
- Database rollback: prefer code rollback first. Destructive persistence retirement is reversible only through its archive-backed Alembic downgrade and a verified backup.
- Frontend rollback: revert Vite chunking and API timeout/request-id changes independently if route loading or API behavior regresses.

## Safety boundaries

- No production database access.
- No production `.env` mutation.
- No production restart.
- No production load or pressure testing.
- No token, secret, cookie, or customer PII exposure in logs, metrics, or artifacts.
