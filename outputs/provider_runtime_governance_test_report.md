# Provider Runtime Governance Test Report

## Commands Run

## CI Failure Follow-Up For PR #413

GitHub Actions failures inspected:

- `backend-ci`: failed in `Run baseline backend tests` at `backend/tests/test_openclaw_local_ops.py::test_admin_connectivity_check_requires_supervisor`. First real error was `HTTPException: 404: OpenClaw legacy integration is disabled`. This was introduced by PR #413 because OpenClaw admin endpoints are now correctly gated off by default. Fix: make the legacy OpenClaw test explicitly opt in with `admin_api.settings.openclaw_integration_enabled = True`.
- `round-a-smoke`: same root cause and same test failure as `backend-ci`.
- `provider-runtime-gate`: failed in `Provider Runtime targeted tests` at `backend/tests/test_admin_provider_runtime_routing_api.py::test_admin_provider_runtime_routing_api_inserts_safe_default`. First real error was an old assertion expecting `codex_app_server` and `openclaw_responses` defaults. This was introduced by PR #413's governance direction. Fix: update the test to assert `codex_direct` with empty fallback.
- `WebCall PR Guard`: first failed in `scope-guard` because the old run saw main-branch patch files in the PR merge diff while this branch was behind `origin/main`; after merging `origin/main`, it failed again because the guard allowlist did not include legitimate provider-runtime governance files touched by PR #413 (`backend/app/api/admin.py`, `backend/app/api/admin_outbound_semantics.py`, `backend/app/schemas.py`, related governance tests, and `outputs/provider_runtime_governance_*.md`). Fix: merge `origin/main` into the PR branch and extend the guard allowlist only for these existing governance files. Local `git diff --name-only origin/main...HEAD` scope simulation now reports 22 changed files and 0 blocked files.

```bash
python -m compileall backend/app
```

Result: passed.

```bash
npm run build
```

Result: passed. Vite emitted the existing large chunk warning for `vendor-livekit`, but build completed.

```bash
pytest backend/tests/test_provider_runtime_default_governance.py -q
```

Result: passed.

```text
5 passed in 6.58s
```

Follow-up rerun after CI fixes:

```bash
python -m compileall backend/app
```

Result: passed.

```bash
npm run build
```

Result: passed from `webapp/`, the package that owns the modified `webapp/src/**` files. Vite emitted the existing large chunk warning for `vendor-livekit`, but build completed.

```bash
pytest backend/tests/test_provider_runtime_default_governance.py -q
```

Result: passed.

```text
5 passed in 1.65s
```

```bash
pytest backend/tests/test_openclaw_local_ops.py backend/tests/test_admin_provider_runtime_routing_api.py -q
```

Result: passed.

```text
4 passed, 1 warning in 3.56s
```

```bash
git diff --name-only origin/main...HEAD
```

Result: passed local WebCall scope simulation with 22 changed files and 0 blocked files.

```bash
docker compose -f deploy/docker-compose.server.yml config --services
```

Result: not executed successfully.

```text
docker compose validation not executed: docker is not installed or not available on PATH in this environment.
```

```bash
docker compose \
  -f deploy/docker-compose.server.yml \
  -f deploy/docker-compose.codex-sidecar.override.yml \
  --profile codex-app-server \
  config --services
```

Result: not executed successfully.

```text
docker compose validation not executed: docker is not installed or not available on PATH in this environment.
```

## Static CI Coverage Added

`backend/tests/test_provider_runtime_default_governance.py` covers:

- Router no-DB-rule default is `codex_direct`.
- `PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]` remains an empty list.
- Production env template requires `codex_direct`, `CODEX_DIRECT_ENABLED=true`, `OPENCLAW_INTEGRATION_ENABLED=false`, and `CODEX_SIDECAR_INTEGRATION_ENABLED=false`.
- Production env template forbids old `codex_app_server` primary, `openclaw_responses` fallback, OpenClaw enabled defaults, and `100.` Tailnet examples.
- Default compose retains required production workers and does not contain sidecar services or OpenClaw/Codex token mounts.
- OpenClaw admin gate returns HTTP 404 when disabled.
