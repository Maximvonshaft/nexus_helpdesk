# Controlled Test Candidate Identity and Readiness Design

## Status

- Work Item: #549
- Delivery class: controlled test deployment
- Baseline: `main@9ae6e9f6aa3742e8576dbe7270a6f17d691dc312`
- Production GO authority: false

## Problem

Current main can report `/readyz` as ready while the running image identity is incomplete or the database Alembic revision does not match the intended candidate. The FastAPI application also reports the historical literal `20.4.0-round-b` instead of the release-provided `APP_VERSION`.

The candidate environment example additionally defaulted Provider and WhatsApp outbound authority to enabled values. Backend feature flags were not sufficient safety boundaries: the Runtime warmer could still read a token and make HTTP requests, and the sidecar could still start the real Baileys connector and auto-login configured accounts.

The first isolated RC run after enabling the migration gate correctly failed: the database was upgraded to `20260711_0058`, but the generated RC environment omitted `EXPECTED_MIGRATION_HEAD`. This showed the readiness gate was correct and the RC environment generator was incomplete.

Independent review additionally established that the warmer must honor the Provider kill switch, and that one apparent migration Head is insufficient if another disconnected or cyclic revision component exists.

## Decision

Introduce one bounded candidate identity/readiness contract:

1. `APP_VERSION` is the application/OpenAPI version authority.
2. `EXPECTED_MIGRATION_HEAD` identifies the exact Alembic head intended for the candidate.
3. `READINESS_REQUIRE_RELEASE_METADATA` controls whether `/readyz` requires complete `GIT_SHA`, `IMAGE_TAG`, `BUILD_TIME` and `FRONTEND_BUILD_SHA` evidence.
4. Production defaults require release metadata and an expected migration head; development remains usable without them.
5. `/readyz` returns bounded migration identity and reason codes and fails closed on missing/mismatched required evidence.
6. Candidate examples keep Provider Runtime, native WhatsApp and outbound dispatch disabled by default.
7. Runtime warmup requires `PRIVATE_AI_RUNTIME_ENABLED=true`, a positive Provider canary percentage and `PROVIDER_RUNTIME_KILL_SWITCH=false` before reading credentials or making a request.
8. The candidate sidecar defaults to the mock connector and an empty auto-start account list.
9. The isolated RC environment generator statically resolves the unique Alembic head from tracked migration files and writes it into the candidate environment.
10. Missing, malformed, duplicate, unknown-parent, multiple-head, cyclic or disconnected Alembic graphs fail before container startup.
11. This slice does not create full Required/Optional/Forbidden business capability profiles and does not authorize deployment or external traffic.

## Readiness contract

`/readyz` evaluates:

- database connectivity;
- observed Alembic revision;
- exact expected/observed migration match when required;
- storage readiness;
- modern frontend build readiness;
- runtime contract signing readiness;
- release metadata completeness when required.

Bounded reason codes:

- `migration_head_required`
- `migration_head_unavailable`
- `migration_head_mismatch`
- `release_metadata_incomplete`
- existing storage/frontend/signing failure logs remain authoritative for those domains.

No credential, database URL, raw exception or release secret is added to the response.

## Alembic head resolution

The RC generator parses tracked Python migration files without importing or executing them. It accepts literal `revision` and `down_revision` assignments, validates every referenced parent, requires exactly one unreferenced revision, walks all parent edges from that Head, rejects reachable cycles, and requires the traversal to cover every parsed revision. This prevents a valid chain from masking a disconnected malformed component.

## Safe candidate defaults

The candidate example retains optional service definitions for isolated testing but defaults all external authority and connectivity off:

- `PRIVATE_AI_RUNTIME_ENABLED=false`
- `PROVIDER_RUNTIME_CANARY_PERCENT=0`
- `PROVIDER_RUNTIME_KILL_SWITCH=true` in isolated RC
- Runtime warmer returns `provider_authority_disabled` before secret/network access when any authority gate is closed
- `WHATSAPP_NATIVE_ENABLED=false`
- `ENABLE_OUTBOUND_DISPATCH=false`
- `OUTBOUND_PROVIDER=disabled`
- `WHATSAPP_DISPATCH_MODE=disabled`
- `WA_SIDECAR_CONNECTOR_MODE=mock`
- `WA_SIDECAR_AUTO_START_ACCOUNTS=`

Enabling any of these requires a separate Work Item, exact candidate review and explicit authorization.

## Protected scope

No model, schema, migration revision, queue, Provider router, WhatsApp implementation, frontend route or deployment action changes.

## Rollback

Revert the delivery merge. Readiness returns to the prior process-health-only behavior and candidate defaults return to their previous values. No database downgrade, data repair, Provider cleanup or customer communication is required.
