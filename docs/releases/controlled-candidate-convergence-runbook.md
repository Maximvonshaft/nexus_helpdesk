# Controlled Candidate Convergence and Swiss Cutover

## Scope

This runbook publishes one exact Nexus binary and prepares a controlled replacement of the current Swiss candidate deployment at `mcs.speedaf.com`.

It does **not** authorize production Provider traffic, real outbound, WhatsApp, SpeedAF writes, Operations Dispatch, production automation, or Issue #533 GO.

## Observed topology

- Swiss application host: `ch-ai-runtime`, Tailscale `100.65.151.82`, GCE private `10.172.0.2`.
- Current application project: `nexusdesk_swiss_candidate`.
- Current application container: `nexusdesk_swiss_candidate-app-candidate-1`.
- Current source: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`.
- Current image ID: `sha256:604a170fbffd57966c165bd76e3b65c5fa1b17cec9784859528e0363361c365a`.
- Current migration: `20260707_0052`.
- PostgreSQL: `10.2.64.2:5432/nexusdesk`.
- Uploads: `/opt/nexus_helpdesk/data/uploads`.
- Upload backup mount: `/var/backups/nexusdesk/uploads`.
- AI token: `/opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token`.
- GPU AI gateway: `http://100.91.119.72:8060` through Tailscale.
- Current Nginx upstream: `127.0.0.1:18094`.
- Controlled candidate upstream: `127.0.0.1:18095`.

The old environment currently enables Provider, outbound, WhatsApp, and SpeedAF write controls. The first controlled cutover must override all of them to disabled before any new container starts.

## GitHub publication

Run `.github/workflows/controlled-candidate-convergence.yml` manually from exact `main`.

The workflow:

1. executes the existing isolated RC chain and builds the application image once;
2. scans and licenses that same local image;
3. pushes that exact image to GHCR;
4. pulls it back by registry digest and proves the image ID is unchanged;
5. runs disposable PostgreSQL recovery qualification on the same source SHA;
6. attaches provenance to the registry digest;
7. emits `controlled-candidate-<sha>` evidence with `nexus.osr.controlled-candidate-manifest.v1`.

Do not deploy a mutable tag. The only acceptable application reference is:

```text
ghcr.io/maximvonshaft/nexus_helpdesk@sha256:<approved-registry-digest>
```

## Swiss replacement sequence

The operator command set is instantiated only after the GitHub workflow returns an accepted source SHA, registry digest, migration revision, and manifest.

### 1. Pre-cutover backup

Create a root-only release backup directory. Capture:

- PostgreSQL custom-format dump and `pg_restore --list` verification;
- uploads archive;
- current Docker inspect metadata;
- current Compose files;
- current Nginx configuration;
- old source/image/migration identity.

No old container is removed before all backup checks pass.

### 2. Build the controlled server environment

Read the existing container environment locally on the server without printing it. Preserve secret-bearing values, then overwrite release identity and every external-effect control with the fail-closed profile in `deploy/.env.controlled.example`.

Required Swiss values:

```text
ALLOWED_ORIGINS=https://mcs.speedaf.com
WEBCHAT_ALLOWED_ORIGINS=https://mcs.speedaf.com
DATABASE_URL=<preserved secret URL to 10.2.64.2:5432/nexusdesk>
NEXUS_UPLOADS_HOST_PATH=/opt/nexus_helpdesk/data/uploads
NEXUS_UPLOAD_BACKUP_HOST_PATH=/var/backups/nexusdesk/uploads
AI_RUNTIME_TOKEN_HOST_PATH=/opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token
PRIVATE_AI_RUNTIME_BASE_URL=http://100.91.119.72:8060
PRIVATE_AI_RUNTIME_RAG_BASE_URL=http://100.91.119.72:8060
CONTROLLED_APP_PORT=18095
```

The first cutover keeps AI and every external action disabled. AI gateway coordinates are retained only for a later separately accepted enablement.

### 3. Preflight

Run:

```bash
python3 scripts/deploy/validate_controlled_server_preflight.py \
  --env-file deploy/.env.controlled \
  --compose-file deploy/docker-compose.controlled.yml \
  --manifest controlled-candidate-manifest.json \
  --expected-database-host 10.2.64.2 \
  --expected-domain mcs.speedaf.com \
  --check-host-paths \
  --output controlled-server-preflight.json
```

Then verify:

```bash
docker compose \
  --env-file deploy/.env.controlled \
  -f deploy/docker-compose.controlled.yml \
  config --quiet
```

Any mutable image, missing host path, unsafe token mode, stale source/migration identity, enabled Provider/outbound flag, or Compose build directive is a hard stop.

### 4. Stop old writers and back up again if traffic changed

Immediately before migration:

1. place Nginx into a controlled maintenance response or stop routing new requests;
2. stop the old Compose project;
3. take the final database dump and uploads delta backup;
4. verify no old worker or sidecar remains running.

### 5. Pull, migrate, and start the digest-only candidate

Pull the exact digest, run the one-off migration service, then start the app and four workers. The Compose file has no `build:` path and no WhatsApp sidecar.

Expected readiness:

- source SHA equals the controlled manifest;
- frontend SHA equals source SHA;
- migration equals the controlled manifest;
- `/healthz` and `/readyz` return 200;
- app and workers are healthy;
- external-effect controls remain disabled.

### 6. Nginx cutover

Only after loopback acceptance, change the existing `mcs.speedaf.com` upstream from `127.0.0.1:18094` to `127.0.0.1:18095`, run `nginx -t`, reload Nginx, and test the public domain.

### 7. Acceptance

Required checks:

- invalid login rejection;
- valid operator login;
- public WebChat initialization and message persistence;
- operator can read the same conversation;
- Provider output count remains zero;
- outbound/WhatsApp/SpeedAF/Dispatch execution remains zero;
- uploads remain readable;
- PostgreSQL and Tailscale routes remain available;
- no secrets appear in logs or evidence.

### 8. Rollback

Rollback consists of:

1. restore Nginx upstream to `127.0.0.1:18094`;
2. stop the controlled project;
3. restore the pre-migration database dump when schema rollback is required;
4. restore uploads when changed;
5. restart the old `nexusdesk_swiss_candidate` project;
6. verify old health and public domain.

Do not use the old application against a database that has not been proven backward compatible after migration.

### 9. Cleanup

Only after independent post-deploy acceptance:

- remove exited old containers;
- remove superseded local candidate directories;
- remove old unreferenced `nexusdesk/helpdesk:mcs-*` images;
- retain the accepted digest, previous rollback image, database dumps, upload archives, manifest, preflight, and acceptance evidence.

Use explicit image IDs. Do not run an unbounded `docker system prune`.
