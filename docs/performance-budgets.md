# NexusDesk Performance Budgets

This document defines the runtime, API, worker, OpenClaw bridge, and frontend budgets for NexusDesk production-readiness review. These budgets are intended for staging verification and ongoing observability. They must not be enforced by production load testing without a separate approved test plan.

## Hard rules

- Do not run production load tests or synthetic pressure tests.
- Do not restart production services as part of budget validation.
- Do not connect directly to the production database for validation.
- Do not write secrets, customer message bodies, SQL parameters, tokens, cookies, or API keys into logs, metrics, image layers, or size reports.
- Do not use high-cardinality labels such as raw SQL, ticket IDs, conversation IDs, customer IDs, email addresses, phone numbers, or message bodies.
- Observability failure must degrade to warning-only behavior and must not break request handling, worker claims, outbound dispatch, or OpenClaw sync.

## API and WebChat budgets

| Surface | Budget | Measurement |
| --- | ---: | --- |
| WebChat init | p95 <= 800 ms | Public init endpoint, staging or production passive telemetry only |
| WebChat poll | p95 <= 300 ms | Public message poll endpoint, staging or production passive telemetry only |
| Operator Console first screen | p95 <= 1.5 s | Browser navigation to first useful render |
| Login | p95 <= 600 ms | `/api/auth/login` |
| Ticket list | p95 <= 500 ms | `/api/lite/cases` or equivalent ticket-list endpoint |
| Ticket summary | p95 <= 700 ms | Ticket summary/details endpoint |
| Ticket timeline/messages | p95 <= 500 ms | Ticket thread/timeline/messages endpoint |
| OpenClaw bridge | p95 <= 2.5 s | Bridge HTTP operation duration by operation/status |
| Worker job wait | p95 <= 30 s | Pending job created/available to claim time |

## Frontend bundle budgets

| Asset class | Budget | Enforcement |
| --- | ---: | --- |
| Single JS chunk gzip | <= 180 KB | `npm --prefix webapp run size-report` |
| First-screen JS gzip | <= 300 KB | `npm --prefix webapp run size-report` conservative estimate |
| Production sourcemap | false by default, hidden only by explicit env | Vite config |

## Runtime budgets and expectations

- Production app runtime should use `gunicorn` with `uvicorn.workers.UvicornWorker`.
- Worker count must be configured through `WEB_CONCURRENCY`; do not hardcode production worker count in application code.
- `WEB_TIMEOUT` must remain configurable and staging-verified before rollout.
- `/healthz` and `/readyz` must remain fast, read-only, and available through both app and nginx paths.
- The app, worker, sync-daemon, and event-daemon must continue to use the same `IMAGE_TAG` for version consistency.

## Nginx cache and compression policy

- `/assets/`: `Cache-Control: public, max-age=31536000, immutable`.
- SPA fallback and `index.html`: `Cache-Control: no-cache`.
- `/api/`: `Cache-Control: no-store`.
- gzip should be enabled for CSS, JavaScript, JSON, plain text, and SVG.
- brotli is deferred until the deployed nginx image is verified to support it.
- upstream keepalive should be enabled for app proxying.

## Observability metrics inventory

Expected low-cardinality metric families include:

- `nexusdesk_http_requests_total`
- `nexusdesk_http_request_duration_ms`
- `nexusdesk_db_query_duration_ms`
- `nexusdesk_db_slow_query_total`
- `nexusdesk_openclaw_bridge_elapsed_ms`
- `nexusdesk_worker_processed_total`
- `nexusdesk_worker_job_duration_ms`
- `nexusdesk_background_job_wait_ms`
- `nexusdesk_worker_oldest_pending_job_age_ms`
- `nexusdesk_queue_depth`
- `nexusdesk_outbound_queued_to_sent_ms`
- `nexusdesk_outbound_provider_dispatch_ms`
- `nexusdesk_outbound_provider_result_total`
- `nexusdesk_frontend_api_latency_ms`
- `nexusdesk_web_vitals_value`

## Staging verification plan

1. Build a staging image from the PR branch with the same `IMAGE_TAG` discipline used in production.
2. Run `docker compose -f deploy/docker-compose.server.yml config` and verify the app command uses gunicorn.
3. Run `bash -n scripts/smoke/runtime_performance_baseline.sh`.
4. Run `scripts/smoke/runtime_performance_baseline.sh` against the staging compose file.
5. Run `python scripts/smoke/worker_daemon_readiness_probe.py` against staging with read-only credentials only if queue/runtime-health endpoints require authentication.
6. Run backend unit tests and Alembic upgrade/downgrade/re-upgrade on a disposable staging database.
7. Run frontend `typecheck`, `build`, and `size-report`.
8. Verify `/healthz` and `/readyz` through both app and nginx routes.
9. Verify nginx headers for `/assets/*.js`, `/`, `/index.html`, `/api/*`, and `/webchat/*`.
10. Verify no secret/env/upload files exist in the built image layers.

## Rollback plan

- Revert the app command to the previous single-process uvicorn command only if gunicorn worker startup fails in staging or post-release smoke checks.
- Roll back the image tag to the last known good image if health/readiness checks fail.
- Do not downgrade production database migrations unless an approved rollback window exists and data compatibility has been confirmed.
- If the `payload_hash` migration has been applied and rollback is required, keep `payload_json` as the replay source of truth; dropping `payload_hash` removes only the idempotency index column.
- If frontend bundle splitting causes route-loading regressions, revert `manualChunks` and retain the API timeout/request-id client changes separately.
