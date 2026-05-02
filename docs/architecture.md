# NexusDesk Architecture

NexusDesk is a case-centric customer operations runtime for logistics support.

Core runtime areas:

- WebChat public entrypoint and customer conversation runtime.
- Operator webapp for authenticated support work.
- FastAPI backend for tickets, admin, WebChat, integration, health, and runtime APIs.
- Worker processes for background jobs and outbound queue handling.
- OpenClaw inbound sync for external conversation capture.
- PostgreSQL schema managed by Alembic migrations.

Key safety boundaries:

- WebChat ACK and safe fallback are local runtime records, not external provider sends.
- OpenClaw inbound auto-sync and outbound dispatch are separate paths.
- External outbound dispatch must remain fail-closed unless explicitly enabled and configured.
- Production schema changes must be represented by Alembic migrations.

Deployment modes:

- `deploy/docker-compose.server.local-postgres.yml` uses an in-compose PostgreSQL service.
- `deploy/docker-compose.server.external-postgres.yml` expects `DATABASE_URL` to point to an external PostgreSQL instance.

Required gates:

- `alembic upgrade head`
- `python scripts/check_model_migration_drift.py`
- `pytest -q`
- `npm run typecheck && npm run build && npm run lint`
- `bash scripts/deploy/check_deploy_contract.sh`
