# Deploy Server External PostgreSQL

Use this environment template when a candidate deployment relies on a managed or external PostgreSQL instance.

Required files:

- deploy/docker-compose.server.yml
- deploy/.env.prod.external-postgres.example

Operational rules:

- `DATABASE_URL` must point to a real external PostgreSQL host, not the compose-only host name `postgres`.
- The current consolidated server compose still defines a local `postgres` service. For a strict external-PostgreSQL production rollout, add a candidate override or compose profile review before cutover.
- Live environment files must stay untracked.
- The default outbound posture must remain disabled.
- Run Alembic migrations before switching traffic.
- Run health checks after starting app and worker.

Required checks:

- bash scripts/deploy/check_deploy_contract.sh
- docker compose --env-file deploy/.env.prod.external-postgres.example -f deploy/docker-compose.server.yml config
- backend Alembic upgrade head
- backend pytest
- healthz and readyz after deployment
