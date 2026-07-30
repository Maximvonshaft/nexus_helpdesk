# Nexus Deployment Runbook

## Current authority

This file is a navigation page. The operational authority is:

- `docs/runbook-production.md` for production posture;
- `docs/runbooks/production-activation.md` for controlled deployment and activation;
- `docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md` for exact-candidate evidence;
- `deploy/nexus-prod-compose.sh` for controlled Compose execution.

The application topology is `deploy/docker-compose.controlled.yml`. A local database adds only `deploy/docker-compose.controlled-postgres.yml`.

Do not restore historical shared environment files, mutable image tags, generic app/worker services, manual runtime launchers or candidate-specific sidecars.

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

These commands render configuration only. Deployment requires explicit authorization after exact-head verification, configuration/data preservation and rollback preparation.

## Service roles

- `app-controlled`: FastAPI API and packaged SPA; owns Web JWT and Metrics access.
- `worker-outbound-controlled`: external outbound queue; disabled in the first cutover.
- `worker-background-controlled`: general background work and authoritative Handoff snapshot projection.
- `worker-webchat-ai-controlled`: AI queue; AI disabled in the first cutover.
- `migrate-controlled`: one-off Alembic role with schema authority.
- `postgres-controlled`: optional local PostgreSQL only; never defines application or Worker authority.

Each long-running service uses its own PostgreSQL identity. Disabled capabilities receive no Provider, AI, Voice or channel credential.

## Deployment and verification

Follow `docs/runbooks/production-activation.md`. After the controlled topology is running, execute `scripts/probe_nexus_runtime.sh`; do not substitute a second health or Worker probe.

## Backup and rollback

Before any cutover, preserve controlled local configuration with:

```bash
bash scripts/deploy/safe_update_server.sh
```

Database restore qualification is owned by:

```text
scripts/qualification/recovery/run_recovery_qualification.sh
```

Image rollback requires a frozen prior controlled environment whose image, source, frontend and migration identity all match the immutable prior digest:

```text
OLD_IMAGE_TAG=ghcr.io/...@sha256:<prior-digest>
ROLLBACK_CONTROLLED_ENV_FILE=<prior-controlled-env>
ROLLBACK_DATABASE_TOPOLOGY=external|local
ROLLBACK_HEALTH_URL=<approved-loopback-url>
ROLLBACK_CONFIRM=I_UNDERSTAND
```

Never overwrite live environment files, server-only Compose/Nginx overrides, database volumes, uploads, backups or secret files merely because repository templates changed.
