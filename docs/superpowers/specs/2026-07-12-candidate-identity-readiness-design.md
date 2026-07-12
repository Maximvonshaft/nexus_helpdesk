# Controlled Test Candidate Identity and Readiness Design

## Status

- Work Item: #549
- Delivery class: controlled test deployment
- Production GO authority: false

## Problem

The application could report `/readyz` as ready while the running image identity was incomplete or the database Alembic revision did not match the intended candidate. FastAPI also reported the historical literal `20.4.0-round-b` instead of the release-provided `APP_VERSION`.

The candidate environment defaulted Provider and WhatsApp outbound authority to enabled values. Backend feature flags were not sufficient safety boundaries: the Runtime warmer could still read a token and make HTTP requests, and the sidecar could still start the real Baileys connector and auto-login configured accounts.

The first isolated RC run after enabling migration binding correctly failed because PostgreSQL had upgraded to `20260711_0058` while the generated environment omitted `EXPECTED_MIGRATION_HEAD`. Independent review then established five additional boundaries:

- Runtime warmup must honor the Provider kill switch;
- one apparent migration Head cannot mask a disconnected/cyclic tracked revision component;
- runtime readiness must inspect every `alembic_version` row rather than `LIMIT 1`;
- empty or NULL `alembic_version` rows must remain visible and fail closed rather than being filtered;
- existing production environments must not become permanently unhealthy merely because migration binding is a new candidate-only control.

## Decision

Introduce one bounded candidate identity/readiness contract:

1. `APP_VERSION` is the application/OpenAPI version authority.
2. `READINESS_REQUIRE_RELEASE_METADATA` independently controls whether `/readyz` requires complete `GIT_SHA`, `IMAGE_TAG`, `BUILD_TIME` and `FRONTEND_BUILD_SHA` evidence.
3. `EXPECTED_MIGRATION_HEAD` opts a runtime into exact migration binding. Candidate and isolated RC paths always supply it; existing production paths that have not adopted migration binding remain compatible.
4. `/readyz` reads the complete `alembic_version` row set. Empty or NULL values are normalized to an explicit invalid observation rather than discarded.
5. When migration binding is active, readiness requires exactly one valid observed Head and compares it to the expected Head.
6. Multiple observed rows fail closed even if one matches the expected value; malformed rows fail closed as `migration_head_invalid`.
7. Candidate examples keep Provider Runtime, native WhatsApp and outbound dispatch disabled by default.
8. Runtime warmup requires `PRIVATE_AI_RUNTIME_ENABLED=true`, a positive Provider canary percentage and `PROVIDER_RUNTIME_KILL_SWITCH=false` before reading credentials or making a request.
9. Both the candidate env and candidate Compose service default the sidecar to the mock connector with an empty auto-start account list.
10. The isolated RC generator statically resolves the unique Alembic Head from tracked migration files and writes it into the candidate environment.
11. Missing, malformed, duplicate, unknown-parent, multiple-head, cyclic or disconnected tracked migration graphs fail before container startup.
12. This slice does not create full Required/Optional/Forbidden business capability profiles and does not authorize deployment or external traffic.

## Readiness contract

`/readyz` evaluates:

- database connectivity;
- the complete observed Alembic version-row set;
- exact expected/observed migration match when `EXPECTED_MIGRATION_HEAD` is supplied;
- storage readiness;
- modern frontend build readiness;
- runtime contract signing readiness;
- release metadata completeness when required.

Bounded migration reason codes:

- `migration_head_invalid`
- `migration_heads_multiple`
- `migration_head_unavailable`
- `migration_head_mismatch`

Other bounded readiness reasons include `release_metadata_incomplete` and the existing storage/frontend/signing failures.

The legacy scalar `migration_revision` remains for compatibility only when exactly one valid database Head exists. Structured `migration.observed_heads` is emitted when the observed set is empty, contains multiple rows or contains an invalid value. No credential, database URL, raw exception or release secret is added to the response.

## Alembic graph resolution

The RC generator parses tracked Python migration files without importing or executing them. It accepts literal `revision` and `down_revision` assignments, validates every referenced parent, requires exactly one unreferenced revision, walks all parent edges from that Head, rejects reachable cycles, and requires the traversal to cover every parsed revision. This prevents a valid chain from masking a disconnected malformed component.

This static tracked-graph validation is separate from runtime database validation. Runtime readiness queries every row in `alembic_version`; extra, empty and NULL rows cannot be hidden by selecting or filtering one matching value.

## Safe candidate defaults

The candidate retains optional service definitions for isolated testing but defaults external authority and connectivity off:

- `PRIVATE_AI_RUNTIME_ENABLED=false`
- `PROVIDER_RUNTIME_CANARY_PERCENT=0`
- `PROVIDER_RUNTIME_KILL_SWITCH=true` in isolated RC
- Runtime warmer returns `provider_authority_disabled` before secret/network access when any authority gate is closed
- `WHATSAPP_NATIVE_ENABLED=false`
- `ENABLE_OUTBOUND_DISPATCH=false`
- `OUTBOUND_PROVIDER=disabled`
- `WHATSAPP_DISPATCH_MODE=disabled`
- candidate env: `WA_SIDECAR_CONNECTOR_MODE=mock`, `WA_SIDECAR_AUTO_START_ACCOUNTS=`
- candidate Compose fallback: mock connector and empty auto-start account list even without an env override

Enabling any external path requires a separate Work Item, exact candidate review and explicit authorization.

## Protected scope

No model, schema, migration revision, queue, Provider router, WhatsApp implementation, frontend route, deployment execution or external resource change.

## Rollback

Revert the delivery merge. Readiness returns to the prior process-health-only behavior and candidate defaults return to their previous values. No database downgrade, data repair, Provider cleanup or customer communication is required.
