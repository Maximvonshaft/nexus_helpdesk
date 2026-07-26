# NexusDesk Runtime Performance Budgets

This document defines the production runtime, infrastructure, observability, worker, provider-adapter, and frontend performance budgets.

## API budgets

- Core health/readiness endpoints should respond quickly under normal staging load.
- Admin list endpoints must use bounded pagination or explicit limits.
- Long-poll / polling endpoints must have bounded wait time and must not create unbounded write amplification.
- Database query timing instrumentation records low-cardinality SQL categories only. SQL parameters and customer content must never be logged or used as labels.
- Frontend API latency telemetry records normalized paths only. Query strings, fragments, customer identifiers and payload data are rejected.

## WebChat budgets

- Public message polling must use throttled `last_seen_at` writes.
- WebChat events polling must use bounded `wait_ms`, stable `after_id`, `limit + 1`, and `has_more` semantics.
- Event write paths that are not the source of truth should be best-effort and must not break primary ticket/conversation state transitions.
- The operator console uses WebSocket event delivery as the primary supported realtime path and bounded HTTP polling only as a fail-safe fallback.

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

`npm run verify` builds the exact frontend and then executes `npm run size-report`; bundle limits are part of the required frontend gate.

The first-screen figure is the gzip sum of the production entry and its complete static import closure from Vite's generated manifest. The verifier must not infer initial loading from filenames or exclude `vendor`, `route` or `lazy` chunks by name. Any JavaScript statically reachable from the entry is first-screen cost regardless of its chunk label.

Heavy task-specific runtimes must remain behind the existing dynamic route graph. In particular, the LiveKit-backed WebCall surface is loaded only when `/webcall/$voiceSessionId` is entered and is statically forbidden from the initial application closure.

## Operator Web Vitals budgets

Production RUM uses the authenticated, bounded `/api/observability/frontend-metrics` endpoint and the existing Prometheus metrics authority. It contains no customer content, user identity, URL query string or high-cardinality resource identifier.

Required p75 targets:

- LCP: at most 2.5 seconds;
- INP: at most 0.2 seconds;
- CLS: at most 0.1.

LCP and INP are stored in seconds. CLS is stored as its unitless score. `good`, `needs-improvement` and `poor` ratings follow the same thresholds used by the frontend observer.

## Representative-volume browser budgets

Canonical browser evidence must include:

- at least 500 queue records delivered through bounded cursor pages;
- at least 300 conversation messages with bounded historical loading;
- keyboard selection and reply composition while background refresh is active;
- slow initial responses and failed background refresh with last server-confirmed information preserved;
- no root horizontal overflow at 320, 375, 768, 1024, 1280, 1366 and 1440 CSS-pixel widths;
- 200% text enlargement without label/value overlap, clipping or hidden required actions;
- semantic, target-size, contrast, Forced Colors and reduced-motion checks across all canonical authenticated routes;
- pixel-regression baselines for normal, loading, empty, degraded, conflict, repair-required and enlarged-text states.

## Staging verification plan

1. Build the server image from the clean branch.
2. Run `docker compose -f deploy/docker-compose.controlled.yml config`.
3. Run `bash -n scripts/smoke/runtime_performance_baseline.sh`.
4. Run `python scripts/smoke/worker_daemon_readiness_probe.py --help`.
5. Deploy to staging only after all canonical gates are green.
6. Verify `/healthz`, `/readyz`, `/metrics`, worker progress freshness, frontend RUM, and provider adapter health in staging.

## Rollback plan

- Prefer code/image rollback first.
- Runtime rollback: revert this PR or roll back the image tag. Restore the previous Uvicorn command only as emergency runtime rollback.
- Nginx rollback: revert `deploy/nginx/default.conf` if routing, cache, or header regressions appear.
- Database rollback: prefer code rollback first. Destructive persistence retirement is reversible only through its archive-backed Alembic downgrade and a verified backup.
- Frontend rollback: revert route splitting, telemetry initialization and API timeout/request-id changes independently if route loading or API behavior regresses.

## Safety boundaries

- No production database access.
- No production `.env` mutation.
- No production restart.
- No production load or pressure testing.
- No token, secret, cookie, URL query, customer PII or free-text content in logs, metrics, or artifacts.
