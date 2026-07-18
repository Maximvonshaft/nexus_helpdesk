# Controlled Candidate Convergence

## Status and authority

This runbook describes a controlled candidate only. It does not authorize
Provider traffic, WebChat AI, voice, real outbound, WhatsApp, SpeedAF writes,
Operations Dispatch, a production deployment or cleanup of the previous system.

The sole implementation and verification authorities are:

- PR `#763` / `fix/722-authority-convergence`;
- `docs/ops/EXACT_HEAD_ACCEPTANCE_RUNBOOK.md`;
- `scripts/verify_repository.py`;
- `scripts/deploy/validate_controlled_server_preflight.py`;
- `deploy/docker-compose.controlled.yml`;
- optional local PostgreSQL overlay:
  `deploy/docker-compose.controlled-postgres.yml`.

There is no GitHub Actions publication path and no candidate-specific App,
Worker, WhatsApp sidecar or runtime-warmer topology.

## 1. Freeze one exact candidate

Record and keep unchanged:

```text
source_sha
source_tree_sha
frontend_build_sha
migration_revision
immutable_image_digest
build_time
app_version
```

Any Head, tree, lockfile, migration, image Digest or deployment-input change
invalidates previous evidence.

## 2. Generate external supply-chain evidence

Build one immutable image and generate its SBOM and signature outside the
repository. Assemble provenance with:

```bash
python scripts/release/assemble_supply_chain_evidence.py \
  --image 'ghcr.io/maximvonshaft/nexus_helpdesk@sha256:<digest>' \
  --sbom-source /secure/evidence/source-sbom.spdx.json \
  --signature-bundle-source /secure/evidence/source-cosign.bundle.json \
  --output-dir /secure/evidence/nexus-<source-sha>
```

Then run the canonical verifier against the same clean Head:

```bash
python scripts/verify_repository.py \
  --release-evidence-dir /secure/evidence/nexus-<source-sha> \
  --evidence-out /secure/evidence/nexus-<source-sha>/repository-verification.json
```

Generated evidence must not be written into the candidate repository.

## 3. Choose one database topology explicitly

### External PostgreSQL

Prepare `deploy/.env.controlled` from `deploy/.env.controlled.example`.
It must contain six distinct service URLs:

```text
DATABASE_URL_MIGRATION
DATABASE_URL_APP
DATABASE_URL_OUTBOUND
DATABASE_URL_BACKGROUND
DATABASE_URL_WEBCHAT_AI
DATABASE_URL_HANDOFF
```

### Local PostgreSQL

Prepare `deploy/.env.controlled.local-postgres` from
`deploy/.env.controlled.local-postgres.example`.
The local overlay initializes one migration role and five long-running service
roles through `deploy/postgres/init-controlled-roles.sh`. It does not duplicate
App or Worker services. The canonical long-running services are `app-controlled`, `worker-outbound-controlled`, `worker-background-controlled`, `worker-webchat-ai-controlled` and `worker-handoff-snapshot-controlled`.

Do not copy `.env.prod*` or `.env.candidate.example`; those paths are bounded
retirement tombstones.

## 4. Preserve the current host before cutover

Run the configuration-only backup helper:

```bash
bash scripts/deploy/safe_update_server.sh
```

It must preserve existing production-local environment and Compose files as
well as any prepared controlled files. Separately create and verify:

- PostgreSQL custom-format backup;
- uploads backup and manifest-equality marker;
- current image/source/migration identity;
- current reverse-proxy configuration.

This step does not authorize stopping or replacing the current system.

## 5. Run controlled preflight

External database example:

```bash
python scripts/deploy/validate_controlled_server_preflight.py \
  --env-file deploy/.env.controlled \
  --compose-file deploy/docker-compose.controlled.yml \
  --manifest /secure/evidence/nexus-<source-sha>/controlled-candidate-manifest.json \
  --expected-database-host <approved-host> \
  --expected-database-port 5432 \
  --expected-domain mcs.speedaf.com \
  --check-host-paths \
  --output /secure/evidence/nexus-<source-sha>/controlled-preflight.json
```

Local database example uses the same preflight with:

```text
--env-file deploy/.env.controlled.local-postgres
--expected-database-host postgres-controlled
```

Before any controlled deployment is accepted, verify the metrics boundary: authenticated `/metrics` returns 200 and unauthenticated `/metrics` returns 401.

The v2 preflight rejects:

- mutable images or mismatched candidate identity;
- one generic database account or duplicate service users;
- a shared Compose `env_file`;
- enabled external effects;
- credentials for disabled Provider/AI/voice/WhatsApp capabilities;
- shared uploads/backup paths;
- placeholder Web or Metrics secrets;
- missing host paths or invalid topology metadata.

## 6. Render, do not deploy

External topology:

```bash
NEXUS_DATABASE_TOPOLOGY=external \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled \
deploy/nexus-prod-compose.sh config --quiet
```

Local topology:

```bash
NEXUS_DATABASE_TOPOLOGY=local \
NEXUS_CONTROLLED_ENV_FILE=deploy/.env.controlled.local-postgres \
deploy/nexus-prod-compose.sh config --quiet
```

Rendering success is not deployment authorization.

## 7. Controlled acceptance requirements

After a separately authorized controlled deployment, evidence must prove:

- image/source/frontend/migration identity matches the frozen candidate;
- App and four dedicated Workers are healthy;
- Queue depth represents database counts, not processed-per-cycle counts;
- stale processing and dead-letter state are visible;
- Support authorization and privacy tests pass on PostgreSQL;
- migration upgrade, rollback and re-upgrade pass;
- backup restore rehearsal records RPO/RTO;
- Provider, AI, voice, outbound, WhatsApp and Operations writes remain zero;
- no secret or customer payload appears in evidence.

## 8. Rollback contract

Database restore is qualified through
`scripts/qualification/recovery/run_recovery_qualification.sh`.

Image rollback requires:

```text
OLD_IMAGE_TAG=<immutable prior digest>
ROLLBACK_CONTROLLED_ENV_FILE=<prior frozen controlled env file>
ROLLBACK_DATABASE_TOPOLOGY=external|local
ROLLBACK_HEALTH_URL=<approved loopback URL>
ROLLBACK_CONFIRM=I_UNDERSTAND
```

The prior environment file must identify the same image Digest, source SHA,
frontend SHA and migration revision. The rollback script will not construct a
release identity by overriding one mutable environment variable.

## 9. Cleanup

Cleanup is a later, separately authorized action. Retain the accepted Digest,
previous rollback Digest, database and uploads backups, candidate manifest,
verification, preflight, recovery and acceptance evidence. Never use an
unbounded Docker prune.
