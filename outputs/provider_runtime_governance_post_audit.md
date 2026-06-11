# Provider Runtime Governance Post-Audit

## Modified Files

- `backend/app/api/admin.py`
- `backend/app/api/admin_outbound_semantics.py`
- `backend/app/api/admin_provider_runtime.py`
- `backend/app/schemas.py`
- `backend/app/services/provider_runtime/router.py`
- `backend/app/settings.py`
- `backend/tests/test_provider_runtime_default_governance.py`
- `deploy/.env.prod.example`
- `deploy/.env.codex-sidecar.example`
- `deploy/.env.openclaw.example`
- `deploy/docker-compose.server.yml`
- `deploy/docker-compose.codex-sidecar.override.yml`
- `deploy/docker-compose.openclaw.override.yml`
- `docs/ops/provider-runtime-governance.md`
- `webapp/src/lib/types.ts`
- `webapp/src/routes/index.tsx`
- `webapp/src/routes/runtime.tsx`
- `outputs/provider_runtime_governance_pre_audit.md`
- `outputs/provider_runtime_governance_test_report.md`

## Reasons By Change Area

- Production env template: default production now matches the real chain `WEBCHAT_FAST_AI_PROVIDER=provider_runtime -> PROVIDER_RUNTIME_PRIMARY_PROVIDER=codex_direct`; old sidecar/OpenClaw defaults are disabled and no Tailnet IP examples remain.
- Optional env examples: Codex sidecar and OpenClaw have explicit opt-in templates with no real tokens, passwords, Tailnet IPs, or internal domains.
- Compose split: default compose now contains only default production services plus existing non-default profiles such as `webcall-ai`; Codex sidecar services and OpenClaw workers/token mounts moved to override files.
- Provider runtime router: no DB rule now defaults to `codex_direct` with empty fallback; explicit JSON `[]` is respected and does not fall through to `WEBCHAT_FAST_AI_FALLBACK_PROVIDER`.
- Settings/API gates: `OPENCLAW_INTEGRATION_ENABLED` and `CODEX_SIDECAR_INTEGRATION_ENABLED` default false. OpenClaw admin actions return 404 when disabled. Readiness/signoff check OpenClaw transport/event driver only when OpenClaw is enabled.
- Frontend gates: default UI no longer calls or displays OpenClaw runtime/connectivity/sync actions unless `VITE_OPENCLAW_INTEGRATION_ENABLED=true`.
- Tests: added governance regression tests to prevent default production from drifting back to sidecar/OpenClaw fallback.

## Final Default Production Chain

```text
WEBCHAT_FAST_AI_ENABLED=true
WEBCHAT_FAST_AI_PROVIDER=provider_runtime
WEBCHAT_FAST_AI_FALLBACK_PROVIDER=none
PROVIDER_RUNTIME_ENABLED=true
PROVIDER_RUNTIME_PRIMARY_PROVIDER=codex_direct
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]
CODEX_DIRECT_ENABLED=true
OPENCLAW_INTEGRATION_ENABLED=false
CODEX_SIDECAR_INTEGRATION_ENABLED=false
```

## Optional Override Usage

Default production:

```bash
docker compose -f deploy/docker-compose.server.yml up -d
```

Codex sidecar experiment:

```bash
docker compose \
  -f deploy/docker-compose.server.yml \
  -f deploy/docker-compose.codex-sidecar.override.yml \
  --profile codex-app-server \
  up -d
```

OpenClaw legacy integration:

```bash
docker compose \
  -f deploy/docker-compose.server.yml \
  -f deploy/docker-compose.openclaw.override.yml \
  up -d
```

## Retained Source Rationale

OpenClaw source, models, migrations, schemas, transcript tables, attachment references, and unresolved-event structures are retained because they protect historical data, rollback paths, and optional legacy integration support.

`codex_direct` is retained because it is the current production provider runtime primary path for WebChat Fast.

## Risk And Rollback

Main risk: deployments that were implicitly relying on sidecar/OpenClaw defaults must now opt in with override files and feature flags.

Rollback path:

- For sidecar: run with `deploy/docker-compose.codex-sidecar.override.yml` and set `CODEX_SIDECAR_INTEGRATION_ENABLED=true`.
- For OpenClaw: run with `deploy/docker-compose.openclaw.override.yml` and set `OPENCLAW_INTEGRATION_ENABLED=true`.
- For router behavior: set explicit DB routing rules or env overrides, but keep `PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]` when no fallback is intended.

## Test Results

- `python -m compileall backend/app`: passed.
- `npm run build`: passed.
- `pytest backend/tests/test_provider_runtime_default_governance.py -q`: passed, 5 tests.
- Docker compose validation: not executed because Docker is not available on PATH in this environment; see `outputs/provider_runtime_governance_test_report.md`.
