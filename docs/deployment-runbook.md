# NexusDesk Deployment Runbook

## Service roles

- `app`: FastAPI API and SPA host.
- `worker`: outbound queue dispatcher and general background jobs.
- `nginx`: public reverse proxy, metrics restriction, health checks.

## Source of truth

- `webapp/` is the current frontend source of truth.
- `frontend_dist/` and `webapp/dist/` are build artifacts and must not be committed.
- `frontend/` is legacy fallback only until the React webapp is fully signed off.

## Runtime modes

- Customer-visible WebChat replies use the unified `private_ai_runtime` provider through Provider Runtime.
- Provider Runtime fallback providers must remain empty in production; backend failure returns no customer-visible text.
- Legacy ExternalChannel runtime settings must remain disabled.
- External customer sends are fail-closed unless `ENABLE_OUTBOUND_DISPATCH=true` and a native/email provider is explicitly enabled.

## Runtime latency posture

- For the current `qwen2.5:3b` Runtime host, keep WebChat AI generation single-lane unless Runtime-side parallel generation has been benchmarked and approved.
- Candidate defaults are tuned for customer-facing latency: `WEBCHAT_AI_TURN_DEBOUNCE_SECONDS=0.05`, `WEBCHAT_AI_WORKER_POLL_SECONDS=0.10`, and `WEBCHAT_AI_WORKER_BUSY_POLL_SECONDS=0.02`.
- Default Ollama output budgets are intentionally concise: short `64`, service `96`, standard `192`, repair `96`.
- Keep customer-facing WebChat on the low-latency direct model. If `qwen3:4b` or another heavier RAG model is enabled through `PRIVATE_AI_RUNTIME_CHAT_MODE=rag|auto`, configure `PRIVATE_AI_RUNTIME_RAG_BASE_URL` to an isolated Runtime host; do not share the low-latency WebChat Ollama slot unless an explicit benchmark approves `PRIVATE_AI_RUNTIME_ALLOW_SHARED_RAG_MODEL=true`.
- If concurrent smoke latency jumps while sequential smoke is fast, treat it as Runtime model contention first. Do not add customer-visible fallback text.

## Safe update flow

```bash
bash scripts/deploy/safe_update_server.sh
bash scripts/deploy/preflight.sh
bash scripts/deploy/backup_postgres.sh ./backups
bash scripts/deploy/run_migrations.sh
docker compose -f deploy/docker-compose.server.yml up -d postgres app worker-outbound worker-background worker-webchat-ai worker-handoff-snapshot nginx
curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1/readyz
docker compose -f deploy/docker-compose.server.yml exec -T app python /app/scripts/smoke/warm_private_ai_runtime.py
```

Run the Runtime warmup after every app/worker restart and before public smoke.
It keeps the first real customer turn from paying Ollama cold-load latency.
Warmup is a gate: if it fails, keep the previous public target or investigate
Runtime health; do not add customer-visible fallback text.

## Outbound Email pilot gate

Keep `ENABLE_OUTBOUND_DISPATCH=false`, `OUTBOUND_PROVIDER=disabled`, and `OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED=false` until the `/outbound-email` admin browser smoke and SMTP test-send gate pass. Follow [Outbound Email Production Pilot Runbook](runbooks/outbound-email-production-pilot.md) before enabling real Email dispatch.

## Rollback flow

```bash
export ROLLBACK_CONFIRM=I_UNDERSTAND
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/helpdesk'
export OLD_IMAGE_TAG='nexusdesk/helpdesk:previous'
bash scripts/deploy/rollback_release.sh ./backups/helpdesk_YYYYMMDD_HHMMSS.sql.gz
```

## Termius / phone operation

Use `tmux`, `screen`, or `nohup` for long commands. Do not run migration or build commands in a fragile mobile SSH session without a persistent terminal.

## Files to protect on servers

Never overwrite these blindly:

- `deploy/.env.prod`
- `deploy/docker-compose.server.yml`
- local Nginx overrides
- local secrets and token files
- database volumes and backups
