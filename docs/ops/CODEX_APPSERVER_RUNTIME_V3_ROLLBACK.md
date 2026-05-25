# Codex App-Server Runtime v3 Rollback

Rollback is configuration-only. The Python 18800 runtime remains deployed and unchanged.

Immediate rollback:

```bash
export CODEX_APP_SERVER_RUNTIME_BACKEND=python_cli_pool
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-private-model-runtime codex-app-server-bridge
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server stop codex-appserver-runtime
```

Expected routing:

`18794 /reply -> http://codex-private-model-runtime:18800/reply`

Do not count fallback responses as v3 Codex success. Metrics and release notes must label rollback traffic as `python_cli_pool`.
