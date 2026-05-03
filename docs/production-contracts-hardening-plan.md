# NexusDesk main Production Contracts Hardening Plan

Branch: `hardening/main-production-contracts`
Base: `main`
Scope: 41 audit findings consolidated into production-grade system contracts.

This document is the engineering execution contract for the hardening branch. It is intentionally organized by production contract rather than by individual audit finding so the implementation remains coherent and reviewable.

## Non-negotiable boundaries

- Do not modify `main` directly.
- Do not bypass outbound dispatch gates: `ENABLE_OUTBOUND_DISPATCH` and `OUTBOUND_PROVIDER` remain authoritative.
- Do not submit real secrets, bridge tokens, S3 keys, provider keys, or credentials.
- Do not redefine TicketStatus semantics or rewrite the core ticket state machine.
- Do not break existing public API compatibility unless a change is explicitly security-hardening and covered by tests.
- Prefer additive endpoints, DTOs, configuration contracts, and migration-safe changes.

## Contract 1 — Permission and identity security

Covers: `ADD-01`, `ADD-03`, `P2-02`.

Target behavior:

- All capability checks that need overrides must pass the active DB session.
- Lite workflow status changes must respect `UserCapabilityOverride`.
- Login throttling must be atomic under concurrent failures.
- Admin password policy must be production-grade.

Required implementation:

- Update `backend/app/services/lite_service.py` so `workflow_update_lite_case()` calls `ensure_can_change_status(current_user, ticket, internal, db)`.
- Grep all `ensure_can_*` usages and ensure runtime paths that need overrides pass `db`.
- Replace select-then-update login throttling with PostgreSQL atomic buckets.
- Add throttle dimensions: username-only, IP-only, username+IP.
- Increase admin password minimum length to 12, or enforce a stronger password policy with clear tests.

Tests:

- User with deny override for `ticket.status.change` must receive 403 through Lite workflow update.
- User without deny override keeps existing behavior.
- Concurrent failed logins produce correct throttle counts.
- Weak admin password is rejected.

## Contract 2 — OpenClaw bridge, attachment, and runtime topology

Covers: `P0-02`, `P0-01`, `P1-07`, `P1-08`, `P2-07`.

Target behavior:

- Remote bridge calls must be authenticated in production.
- OpenClaw attachment ingestion must have hard size, MIME, and extension limits.
- OpenClaw deployment mode must be explicit and fail-fast.
- Sync/event daemon expectations must match runtime configuration.

Required implementation:

- Introduce a unified `OpenClawBridgeClient` wrapper for all bridge HTTP calls.
- Support Bearer token first; optionally HMAC + timestamp later.
- Production `remote_gateway` + bridge mode must require bridge auth.
- Decode base64 attachments only after size estimation.
- Recheck decoded bytes size.
- Pass `max_bytes`, `allowed_mime_types`, and `allowed_extensions` to `persist_bytes()` for OpenClaw attachments.
- Apply max bytes to text/caption fallback attachments as well.
- Define a deployment mode matrix: `disabled`, `local_gateway`, `remote_gateway_bridge`, `remote_gateway_mcp`.
- Add readiness/signoff checks for daemon heartbeats and bridge reachability.

Tests:

- No token bridge request fails.
- Wrong token fails.
- Correct token passes.
- Oversized base64 attachment is rejected and no file is written.
- Production invalid OpenClaw mode combinations fail fast.

## Contract 3 — PostgreSQL, Alembic, and runtime signoff

Covers: `P1-01`, `P1-02`.

Target behavior:

- CI proves the app can migrate and start against real PostgreSQL.
- `/readyz` remains lightweight.
- Deep production readiness is exposed under an authenticated runtime endpoint.

Required implementation:

- Update `.github/workflows/backend-ci.yml` with a PostgreSQL service.
- Run `alembic upgrade head` and `alembic current`.
- Start FastAPI and smoke `/healthz`, `/readyz`, login, and at least one core API.
- Keep `/readyz` lightweight.
- Add `/api/admin/runtime/deep-readiness` protected by `runtime.manage`.
- Deep readiness checks Alembic head, storage, worker heartbeat, OpenClaw bridge, daemon heartbeats, and queue backlog.

Tests:

- CI fails if migration fails.
- Deep readiness reports missing worker/daemon/storage/bridge explicitly.

## Contract 4 — Public entry rate limits and browser security

Covers: `P1-05`, `P1-06`, `P1-03`, `P2-06`, `P1-04`.

Target behavior:

- WebChat and Integration limits are atomic under concurrency.
- WebChat public availability is explicit.
- Real client IP parsing is shared.
- CSP supports valid cross-origin deployments without wildcarding.

Required implementation:

- Use PostgreSQL atomic rate-limit buckets for WebChat.
- Use PostgreSQL atomic rate-limit buckets for Integration.
- Keep request logs for audit only, not real-time limiting.
- Add `WEBCHAT_PUBLIC_ENABLED` or equivalent explicit contract.
- In production, public WebChat enabled requires non-empty `WEBCHAT_ALLOWED_ORIGINS` and no localhost.
- WebChat must use shared `get_client_ip(request)`.
- Add `CSP_CONNECT_SRC`, default self, reject wildcard in production.

Tests:

- Concurrent WebChat calls cannot exceed configured threshold.
- Concurrent Integration calls cannot exceed configured threshold.
- Empty WebChat origins fail in production when public enabled.
- CSP permits configured API origin and blocks unspecified origins.

## Contract 5 — API validation, pagination, and idempotency

Covers: `P1-09`, `P2-04`, `P2-08`, `ADD-05`, `P2-05`.

Target behavior:

- API layer rejects invalid data before database exceptions.
- List endpoints share consistent pagination limits.
- Idempotency is reserved before side effects.
- Lite intake duplicate creation is prevented at database level.

Required implementation:

- Align external request schemas with SQLAlchemy `String(length)` constraints.
- Introduce shared pagination dependency with `limit <= 200` and `offset >= 0`.
- Implement Integration idempotency reservation rows.
- Handle same key + same payload as replay/in-progress.
- Handle same key + different payload as 409.
- Add Lite intake dedupe key/table with unique constraint.
- Declare attachment visibility as enum Form parameter.

Tests:

- Oversized fields return 422, not 500.
- Huge list limit returns 422.
- Concurrent same Idempotency-Key executes once.
- Concurrent same Lite intake creates one open case.

## Contract 6 — Heavy resource pagination and summary DTOs

Covers: `P1-10`, `P2-10`, `P2-11`, `ADD-08`, `ADD-09`.

Target behavior:

- Large ticket, knowledge, persona, and AI config resources are paginated.
- List responses use lightweight DTOs.

Required implementation:

- Split Ticket detail into core detail plus paginated timeline, attachments, transcript, outbound.
- Add Knowledge summary DTO for list, excluding body fields.
- Add paginated version endpoints for Knowledge, AI Config, and Persona.
- Persona resolve should filter candidates in SQL before ranking.

Tests:

- Large ticket detail remains bounded.
- Knowledge list does not include body fields.
- Version lists paginate correctly.

## Contract 7 — Storage and attachment lifecycle

Covers: `P2-01`, `ADD-10`, `P0-01`.

Target behavior:

- Local and S3 storage produce clean metadata.
- S3 mode fails fast if misconfigured.
- Downloads are auditable.

Required implementation:

- Validate S3 required config in production.
- Validate `boto3` availability when using S3.
- Set file_path to `None` when storage absolute path is `None`.
- Record local downloads as actual downloads.
- Record S3 presigned URL issuance as `download_url_issued`.

Tests:

- S3 without bucket fails startup in production.
- S3 upload does not store string `"None"`.
- Download emits audit evidence.

## Contract 8 — AI provider and AI config governance

Covers: `P2-09`, `ADD-07`.

Target behavior:

- AI calls are provider-governed, observable, and kill-switchable.
- Published AI config is schema-valid.

Required implementation:

- If `llm_service.py` is legacy, disable it in production and mark deprecated.
- Otherwise route it through a unified `AIProvider` interface.
- Provider contract includes provider, model, timeout, redaction, metrics, kill switch, and audit context.
- AI config publish validates by `config_type` schema.
- Snapshots include `schema_version`.

Tests:

- Missing CLI does not break production if legacy is disabled.
- Malformed AI config cannot publish.
- Valid config publishes with schema version.

## Contract 9 — Audit, observability, and log sanitization

Covers: `P1-11`, `P2-14`, `ADD-02`, `P3-03`, `ADD-10`.

Target behavior:

- Metrics labels must not include high-cardinality business IDs.
- Admin mutation audit must be complete and request-context aware.
- Logs must not leak raw exception details by default.

Required implementation:

- Record metrics path using FastAPI route template.
- Add request context helper: request_id, client_ip, user_agent, endpoint, method, result.
- Extend `log_admin_audit()` to accept request context.
- Cover users, capabilities, markets, channel accounts, AI config, bulletins, runtime requeue.
- Add `sanitize_exception(exc)` for production logging.

Tests:

- WebChat conversation IDs do not appear in metrics path labels.
- Admin mutation creates audit row with request context.
- Sanitized logs do not include secret-like values.

## Contract 10 — Frontend, CSP, healthz, and edge hardening

Covers: `P2-12`, `P1-04`, `P2-13`, `ADD-04`, `P3-02`.

Target behavior:

- Frontend runtime failures are recoverable.
- CSP and health endpoints are production-safe.
- SPA fallback cannot escape frontend root.
- Edge config is explicit.

Required implementation:

- Add global ErrorBoundary and route errorComponent.
- Minimize `/healthz` to a simple status response.
- Move build identity to authenticated admin runtime endpoint.
- Guard SPA fallback with `resolve().relative_to(frontend_dir.resolve())`.
- Add hardened Nginx public-edge template or clearly document external LB/BT responsibility.

Tests:

- Route crash shows error UI, not white screen.
- Healthz does not expose build identity.
- Encoded traversal paths return 404.

## Contract 11 — Engineering governance and structured WebChat idempotency

Covers: `P2-03`, `P3-01`, `P3-04`.

Target behavior:

- CI is non-duplicative.
- Supply chain is auditable.
- WebChat action idempotency is structure-based.

Required implementation:

- Parameterize Docker mirrors with build args.
- Add SBOM and vulnerability scan jobs.
- Merge duplicate frontend workflows.
- Add structured card/action fields and unique index for WebChat idempotency.
- Keep JSON LIKE fallback during migration.

Tests:

- Single frontend CI remains authoritative.
- Scan job runs.
- Duplicate WebChat action resolves by structured unique key.

## Atomic commit plan

1. `fix: enforce permission overrides and auth throttling contracts`
2. `fix: harden openclaw bridge auth and runtime topology`
3. `ci: add postgres migration and runtime signoff gates`
4. `fix: make public entry rate limits atomic and origins explicit`
5. `fix: enforce api validation pagination and idempotency contracts`
6. `perf: paginate heavy resources and split large payloads`
7. `fix: harden storage and attachment lifecycle contracts`
8. `feat: introduce ai provider and config schema governance`
9. `feat: complete audit observability and log sanitization contracts`
10. `fix: harden frontend runtime and edge security contracts`
11. `chore: clean ci supply chain and structured webchat idempotency`

## Required final report

The implementation PR must include:

- Modified file list.
- Migration list.
- Contract-to-issue mapping.
- Test commands.
- CI result summary.
- Runtime verification notes.
- Remaining risk list.
- Rollback strategy.
