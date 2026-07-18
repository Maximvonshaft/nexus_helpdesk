# Nexus Deployment Runbook

## Current authority

This file is a compatibility navigation page. The current operational authority
is `docs/runbook-production.md`, with exact-candidate evidence defined by
`docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md`.

The sole application topology is `deploy/docker-compose.controlled.yml`.
A local database adds only `deploy/docker-compose.controlled-postgres.yml`.

Retired concepts that must not be used:

- generic `app`, `worker`, `runtime-warmer` or candidate service names;
- `docker-compose.server.yml` service definitions;
- shared `.env.prod` injection;
- mutable image tags;
- automatic Runtime warmup as a release gate;
- candidate-specific WhatsApp sidecars;
- production enablement of Provider, AI, voice or outbound as part of deployment.

## Configuration rendering

External PostgreSQL:

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh config --quiet
```

Local PostgreSQL:

```bash
NEXUS_DATABASE_TOPOLOGY=local \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled.local-postgres \
deploy/nexus-prod-compose.sh config --quiet
```

These commands render configuration only. Deployment requires separate explicit
authorization after exact-head verification, recovery qualification and
independent Review.

## Service roles

- `app-controlled`: FastAPI API and packaged SPA; owns Web JWT and Metrics access.
- `worker-outbound-controlled`: external outbound queue; disabled in first cutover.
- `worker-background-controlled`: general background jobs and authoritative queue snapshots.
- `worker-webchat-ai-controlled`: AI queue; AI disabled in first cutover.
- `worker-handoff-snapshot-controlled`: handoff snapshots.
- `migrate-controlled`: one-off Alembic role with schema authority.
- `postgres-controlled`: optional local PostgreSQL only; never defines App/Workers.

Each long-running service uses a distinct PostgreSQL identity. Disabled
capabilities receive no Provider, AI, voice or WhatsApp credential.

## Backup and rollback

Before any cutover, preserve production-local files with:

```bash
bash scripts/deploy/safe_update_server.sh
```

Database restore qualification is owned by:

```text
scripts/qualification/recovery/run_recovery_qualification.sh
```

Image rollback requires a frozen prior controlled environment whose image,
source, frontend and migration identity all match the immutable prior Digest:

```text
OLD_IMAGE_TAG=ghcr.io/...@sha256:<prior-digest>
ROLLBACK_CONTROLLED_ENV_FILE=<prior-controlled-env>
ROLLBACK_DATABASE_TOPOLOGY=external|local
ROLLBACK_HEALTH_URL=<approved-loopback-url>
ROLLBACK_CONFIRM=I_UNDERSTAND
```

Never overwrite these production-local assets merely because repository files
changed:

- existing `.env.prod` or controlled env files;
- server-local Compose/Nginx overrides;
- database volumes and backups;
- uploads and uploads backups;
- secret files.
