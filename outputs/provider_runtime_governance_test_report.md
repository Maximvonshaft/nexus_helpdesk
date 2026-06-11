# Provider Runtime Governance Test Report

## Commands Run

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
