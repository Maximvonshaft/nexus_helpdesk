# Codex App-Server Runtime v3 Runbook

Start with rollback default:

```bash
export CODEX_APP_SERVER_RUNTIME_BACKEND=python_cli_pool
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-private-model-runtime codex-app-server-bridge
```

Start v3 candidate:

```bash
export CODEX_APP_SERVER_RUNTIME_BACKEND=node_appserver
export CODEX_APPSERVER_RUNTIME_ENABLED=true
export CODEX_APPSERVER_MODEL=gpt-5.5
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-appserver-runtime codex-app-server-bridge
```

Health checks:

```bash
curl -fsS http://127.0.0.1:18810/healthz
curl -fsS http://127.0.0.1:18810/readyz
curl -fsS http://127.0.0.1:18794/readyz
```

Server validation after owner provides a controlled valid token:

```bash
bash scripts/probe_codex_appserver_discovery.sh
bash scripts/probe_codex_appserver_runtime_v3_sla.sh
```

WebChat enablement requires both runtime config and DB rollout state:

```bash
export WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
# DB canary > 0 is also required before customer WebChat traffic routes to codex_app_server.
```

Canary remains 0 by default. Do not increase customer canary until discovery, dummy negative, valid-token positive, SLA, audit, and runtime log checks all pass.

## Server Validation Snapshot

Validated PR image:

- Image: `nexusdesk/helpdesk:pr233-codex-v3-20260525T103424Z`
- SHA: `8ded81f5b63ccb6214b25d1cec21da939710ae9c`
- Runtime command profile: `CODEX_APPSERVER_COMMAND=/usr/local/lib/node_modules/@openclaw/codex/node_modules/.bin/codex`
- Runtime model: `CODEX_APPSERVER_MODEL=gpt-5.5`

Observed results:

- Direct 18810 valid-token probe passed.
- Dummy token negative passed with no assistant reply.
- 18794 `node_appserver` route passed for valid token and failed closed for dummy token.
- Controlled single WebChat request passed with `reply_source=codex_app_server`, `ai_generated=true`, and `intent=tracking_missing_number`.
- Restore ran after the controlled probe. Canary remains 0 by default.
- 6-parallel pilot SLA passed functionally with p95 about 9092 ms.
- 12-parallel SLA still had errors after tuning.

Current status: pilot-functional only. Production no-go remains for broad canary, high parallel traffic, p95 above 8 seconds, and 12-parallel errors.
