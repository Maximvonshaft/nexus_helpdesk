# Deploy Server External PostgreSQL

Use this mode for production deployments that rely on a managed or external PostgreSQL instance.

Required files:

- deploy/docker-compose.server.external-postgres.yml
- deploy/.env.prod.external-postgres.example

Operational rules:

- The compose stack must not define a postgres service.
- DATABASE_URL must point to a real external PostgreSQL host, not the compose-only host name postgres.
- Live environment files must stay untracked.
- The default outbound posture must remain disabled.
- Run Alembic migrations before switching traffic.
- Run health checks after starting app and worker.

Required checks:

- bash scripts/deploy/check_deploy_contract.sh
- docker compose config with the external-postgres compose file
- backend Alembic upgrade head
- backend pytest
- healthz and readyz after deployment
