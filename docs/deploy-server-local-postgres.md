# Deploy Server Local PostgreSQL

Use this mode for controlled single-server or VM pilot deployments.

Required files:

- deploy/docker-compose.server.yml
- deploy/.env.prod.local-postgres.example

Operational rules:

- The compose stack owns a PostgreSQL service named postgres.
- The database URL may use host postgres only in this local-postgres mode.
- Live environment files must stay untracked.
- The default outbound posture must remain disabled.
- Run Alembic migrations before switching traffic.
- Run health checks after starting app and worker.

Required checks:

- bash scripts/deploy/check_deploy_contract.sh
- docker compose --env-file deploy/.env.prod.local-postgres.example -f deploy/docker-compose.server.yml config
- backend Alembic upgrade head
- backend pytest
- healthz and readyz after deployment
