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

Do not increase customer canary until discovery, dummy negative, valid-token positive, SLA, audit, and runtime log checks all pass.
