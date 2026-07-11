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

## Runtime capability and latency posture

- The active generation model is configured through `PRIVATE_AI_RUNTIME_GENERATION_MODEL` and must exactly match the authenticated `nexus.ai_runtime.capabilities.v1` manifest and the approved candidate expectation.
- Retrieval is an independent capability. Its backend, embedding model and dimension, reranker, and active collection alias must match the approved manifest; do not represent RAG as a second generation model.
- Candidate defaults are tuned for customer-facing latency: `WEBCHAT_AI_TURN_DEBOUNCE_SECONDS=0.05`, `WEBCHAT_AI_WORKER_POLL_SECONDS=0.10`, and `WEBCHAT_AI_WORKER_BUSY_POLL_SECONDS=0.02`.
- Default Ollama output budgets are intentionally concise: short `24`, service `96`, standard `192`, repair `96`.
- Run the authenticated capability probe before generation Smoke or warmup. A mismatch blocks cutover; do not change expectations merely to accept an unexpected Runtime.
- If concurrent smoke latency jumps while sequential smoke is fast, treat it as Runtime contention first. Do not add customer-visible fallback text.

See [Private AI Runtime Rollout Runbook](ops/PRIVATE_AI_RUNTIME_ROLLOUT_RUNBOOK.md) for exact contract, token rotation, Smoke and traffic gates.

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

Run Runtime capability verification and warmup after every relevant Runtime or app/worker restart and before public smoke. Warmup is a gate: if it fails, keep the previous public target or investigate Runtime identity/health; do not add customer-visible fallback text.

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
- Runtime capability manifest and token files
- local Nginx overrides
- local secrets and token files
- database volumes and backups
