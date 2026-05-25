# deploy/AGENTS.md — Deployment Execution Contract

This contract applies to `deploy/**`. Deployment files can affect live service availability, secrets, persistence, networking, and rollback. Treat every change here as production-risk unless it is clearly docs-only.

## 1. Mandatory inspection before deployment changes

Inspect:

```text
Dockerfile
backend/app/settings.py
backend/app/main.py
backend/requirements.txt
webapp/package.json
tools/nexus-codex-runtime/package.json
deploy/docker-compose.server.example.yml
deploy/docker-compose.cloud.yml if present
deploy/.env.prod.example
scripts/deploy/**
docs/ops/**
README.md deployment notes
```

## 2. Live-state separation rule

Never commit or overwrite live state:

```text
deploy/.env.prod
real secrets
/run/secrets content
data/
uploads / local storage roots
server-only compose override files
private Nginx/TLS files
private token files
Tailscale addresses or private gateway URLs
```

Templates may be committed. Real environment files must not.

## 3. Dockerfile rules

The Dockerfile builds:

```text
webapp builder
nexus-codex-runtime builder
openclaw runtime
Python backend runtime
```

Do not regress:

```text
webapp build from source
nexus-codex-runtime npm build
OpenClaw/Codex availability checks
backend requirements install
copy only deterministic source paths
frontend_dist generated inside image
non-root appuser runtime
healthcheck on /healthz
```

Do not `COPY . .` into the image. That can bake local caches, VCS metadata, env files, uploads, or secrets.

## 4. Compose rules

For server-style compose templates, preserve:

```text
PostgreSQL healthcheck
app bound to 127.0.0.1 host port by default
APP_ENV=production
AUTO_INIT_DB=false
SEED_DEMO_DATA=false
OPENCLAW_CLI_FALLBACK_ENABLED=false
WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT=false
/run/secrets mounted read-only
uploads mounted explicitly
worker process
sync-daemon process
event-daemon process
restart policy
```

Do not expose the app container publicly unless the reverse proxy, CORS, TLS, headers, and firewall model are explicitly reviewed.

## 5. Secret custody

Prefer:

```text
/run/secrets/*
Docker/host secret files
env examples with placeholder names only
```

Avoid:

```text
real tokens in YAML
real tokens in README/runbooks
printing secrets in shell scripts
embedding private upstream URLs
```

## 6. Deployment validation template

Do not run on a live production host without explicit user approval.

```bash
set -Eeuo pipefail
docker compose -f deploy/docker-compose.server.yml build
docker compose -f deploy/docker-compose.server.yml run --rm app alembic upgrade head
docker compose -f deploy/docker-compose.server.yml up -d
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

## 7. Rollback evidence

Deployment PRs must include:

```text
previous image tag or commit
new image tag or commit
migration impact
whether rollback is code-only or requires DB restore/forward fix
health checks
log checks
Nginx/proxy impact
secret/env changes
operator action required
```

## 8. Hard stops

Stop before any change that:

```text
deletes volumes or data
changes real production env files
enables public access to private sidecars
enables CLI fallback in production
turns on Codex/Speedaf/customer outbound without feature flag and approval
weakens production settings validation
removes health/readiness checks
```
