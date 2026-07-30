# NexusDesk Runtime Performance Budgets

This document defines the production runtime, infrastructure, observability, Worker, Provider-adapter and frontend performance budgets.

## API budgets

- Core health/readiness endpoints should respond quickly under normal staging load.
- Admin list endpoints use bounded pagination or explicit limits.
- Long-poll endpoints have bounded wait time and must not create unbounded write amplification.
- Database timing instrumentation uses low-cardinality SQL categories only. SQL parameters and customer content are not labels or logs.
- Frontend latency telemetry records normalized paths only. Query strings, fragments, customer identifiers and payload data are rejected.

## WebChat budgets

- Public message polling uses throttled `last_seen_at` writes.
- Event polling uses bounded `wait_ms`, stable `after_id`, `limit + 1` and `has_more` semantics.
- Best-effort event projections must not break primary Conversation/Ticket transitions.
- WebSocket delivery is the primary operator realtime path; bounded HTTP polling is only a fail-safe fallback.

## Provider adapter budgets

- Provider adapters use pooled clients where applicable.
- Timeouts and connection limits are explicit and environment-tunable.
- Timeout, invalid response, HTTP and transport failures degrade safely with bounded error codes.
- Adapter logs scrub credentials and customer content.
- Disabled adapters never activate an alternate provider path.

## Worker budgets

- Worker health proves durable progress freshness, not only process existence.
- Runtime probes reject destructive operations.
- Worker metrics track job duration, wait time, retries and oldest pending age with low-cardinality labels.
- Queue ownership must match the rendered controlled Compose topology.

## Frontend bundle budgets

Default CI budgets:

- largest single JavaScript chunk gzip: 180 KB;
- first-screen JavaScript gzip: 300 KB.

`npm run verify` builds the exact frontend and runs the size report. The first-screen figure is the gzip sum of the production entry and its complete static import closure. Heavy task-specific runtimes stay behind dynamic routes; LiveKit-backed WebCall code must not enter the initial application closure.

## Operator Web Vitals budgets

Production RUM uses the authenticated, bounded `/api/observability/frontend-metrics` endpoint and the Prometheus Metrics authority. It contains no customer content, user identity, query string or high-cardinality resource identifier.

Required p75 targets:

- LCP: at most 2.5 seconds;
- INP: at most 0.2 seconds;
- CLS: at most 0.1.

## Representative-volume browser budgets

Canonical browser evidence includes:

- at least 500 queue records delivered through bounded cursor pages;
- at least 300 Conversation messages with bounded historical loading;
- keyboard selection/reply composition while background refresh is active;
- slow initial responses and failed background refresh with last confirmed state preserved;
- no root horizontal overflow at 320, 375, 768, 1024, 1280, 1366 and 1440 CSS-pixel widths;
- 200% text enlargement without overlap, clipping or hidden required actions;
- semantic, target-size, contrast, Forced Colors and reduced-motion checks;
- loading, empty, degraded, conflict, repair-required and enlarged-text assertions.

Screenshots may support diagnosis, but screenshot hashes and pixel baselines are not release authority.

## Staging verification plan

1. Build the immutable server image from the frozen source.
2. Render the canonical controlled topology with `deploy/nexus-prod-compose.sh config --quiet`.
3. Run `bash scripts/smoke/runtime_performance_baseline.sh`.
4. Deploy only after Canonical Acceptance and controlled preflight pass.
5. Run `scripts/probe_nexus_runtime.sh` against the deployed loopback endpoint.
6. Verify health, readiness, Metrics authentication, Worker progress, queue age, frontend RUM and enabled Provider health.
7. Execute load/pressure tests only on the designated staging or capacity environment, never against live customer traffic without explicit authorization.

## Rollback plan

- Prefer immutable image rollback first.
- Restore the previous controlled environment and run the canonical rollback script.
- Revert Nginx only when routing, cache or header behavior is the actual root cause.
- Prefer code rollback before database restore; restore only from a verified backup/rehearsal path.
- Roll back frontend assets together with the matching backend image identity.

## Safety boundaries

- No production pressure test by default.
- No uncontrolled environment mutation.
- No blind production restart.
- No token, secret, cookie, URL query, customer PII or free text in logs, Metrics or artifacts.
