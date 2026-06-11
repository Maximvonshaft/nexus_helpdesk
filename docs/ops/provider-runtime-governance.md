# Provider Runtime Governance

## Default Production Path

Default production uses WebChat Fast through provider runtime with Codex Direct:

```bash
docker compose -f deploy/docker-compose.server.yml up -d
```

Expected default values:

```text
WEBCHAT_FAST_AI_PROVIDER=provider_runtime
WEBCHAT_FAST_AI_FALLBACK_PROVIDER=none
PROVIDER_RUNTIME_PRIMARY_PROVIDER=codex_direct
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]
CODEX_DIRECT_ENABLED=true
OPENCLAW_INTEGRATION_ENABLED=false
CODEX_SIDECAR_INTEGRATION_ENABLED=false
```

Codex Direct must run with an isolated `CODEX_DIRECT_HOME` under uploads, no repository checkout requirement, and no unintended application secrets exposed in the subprocess environment.

## Optional Codex Sidecar

The Codex sidecar path is experimental and not part of default production. To test it, copy `deploy/.env.codex-sidecar.example` into the server env management flow, then run:

```bash
docker compose \
  -f deploy/docker-compose.server.yml \
  -f deploy/docker-compose.codex-sidecar.override.yml \
  --profile codex-app-server \
  up -d
```

This changes provider runtime to `codex_app_server` only for that override.

## Optional OpenClaw Legacy Integration

OpenClaw remains as legacy / optional integration. Source code, models, migrations, schemas, transcripts, and unresolved-event structures are retained for historical data and rollback safety, but default production does not start OpenClaw services or expose OpenClaw admin actions.

To test OpenClaw, copy `deploy/.env.openclaw.example` into the server env management flow, keep writes and auto-sync disabled until explicitly approved, then run:

```bash
docker compose \
  -f deploy/docker-compose.server.yml \
  -f deploy/docker-compose.openclaw.override.yml \
  up -d
```

Enable OpenClaw UI/API exposure only with:

```text
OPENCLAW_INTEGRATION_ENABLED=true
VITE_OPENCLAW_INTEGRATION_ENABLED=true
```

Do not commit real tokens, Tailnet IPs, internal domains, or passwords.
