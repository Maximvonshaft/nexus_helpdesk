# Nexus controlled candidate publication

## Decision boundary

Nexus uses three distinct authorities. They are sequential and do not duplicate one another:

1. `.github/workflows/canonical-acceptance.yml` proves an exact software candidate.
2. `.github/workflows/controlled-candidate-convergence.yml` publishes the already bounded candidate to GHCR, pulls it back, proves binary identity, produces recovery evidence and creates the controlled-candidate manifest.
3. `deploy/docker-compose.controlled.yml` and the production-activation overlay govern a separately authorized target-server deployment and capability activation.

The publication workflow does **not** deploy a server and does **not** authorize Provider traffic, WebChat AI, Voice, outbound dispatch, WhatsApp, Speedaf writes or Operations Dispatch.

## Publication trigger

The controlled publication workflow is main-only and supports two bounded entry points:

- an explicit GitHub `workflow_dispatch` on `main`;
- the deterministic request bridge, triggered only when `.github/controlled-candidate-request.json` is the sole changed file in a new `main` commit.

The bridge performs no build, push, deployment or external business action. It validates the exact `main` commit and dispatches the one controlled-candidate workflow. Its request must keep:

```text
deployment_authorized=false
production_authority=false
external_actions_authorized=false
issue_533=NO_GO
```

The bridge writes the exact source SHA and workflow Run ID to governance Issue #724.

## Controlled publication workflow

The workflow is restricted to the current `main` SHA and uses least-privilege job permissions.

It performs:

1. exact-main identity verification;
2. the existing one-build RC chain;
3. runtime import and Gunicorn configuration checks;
4. Trivy vulnerability inventory;
5. image and frontend CycloneDX SBOM generation;
6. release-image assurance and compliance binding;
7. GHCR publication under `controlled-<source-sha>`;
8. digest pullback and local/pulled image-ID equality proof;
9. PostgreSQL backup/restore recovery qualification;
10. GitHub build-provenance attestation bound to the exact registry digest;
11. final controlled-candidate manifest and bounded artifact scan.

No second application image is built for publication. The exact assured image is tagged, pushed, pulled by digest and compared to the original local image ID.

## Required output

A successful run produces:

```text
controlled-candidate-<source-sha>/
  candidate-manifest.json
  release-image-manifest.json
  release-image-compliance-binding.json
  registry-publish-receipt.json
  recovery-evidence.json
  controlled-candidate-manifest.json
  artifact-scan.json
```

The final manifest must bind:

- source SHA;
- frontend SHA;
- Alembic migration revision;
- immutable GHCR digest;
- local and registry-pulled image IDs;
- build time and application version;
- SBOM and image-assurance evidence;
- recovery evidence;
- GitHub provenance attestation.

A controlled candidate remains:

```text
production_ready=false
deployment_performed=false
external_effects_authorized=false
```

Publication is a prerequisite for deployment, not deployment authorization.

## Target-server preparation

After publication, populate `deploy/.env.controlled` from the example using the exact manifest values:

```text
CONTROLLED_IMAGE=<registry image@sha256 digest>
IMAGE_TAG=<same immutable digest>
GIT_SHA=<manifest source sha>
FRONTEND_BUILD_SHA=<same source sha>
EXPECTED_MIGRATION_HEAD=<manifest migration revision>
BUILD_TIME=<manifest build time>
APP_VERSION=<manifest app version>
```

Supply real process-specific PostgreSQL accounts, runtime secrets, origins, metrics token, storage paths and backup marker. Do not commit credentials.

Run the controlled-server preflight against the exact manifest before starting containers:

```bash
python scripts/deploy/validate_controlled_server_preflight.py \
  --env-file deploy/.env.controlled \
  --compose-file deploy/docker-compose.controlled.yml \
  --manifest /secure/evidence/controlled-candidate-manifest.json \
  --expected-database-host <approved-host> \
  --expected-database-port 5432 \
  --expected-domain mcs.speedaf.com \
  --check-host-paths \
  --output /secure/evidence/controlled-preflight.json
```

The controlled profile must keep Provider, WebChat AI, Voice, outbound and Operations effects disabled.

## Production activation

Customer-facing activation is governed separately by `docs/runbooks/production-activation.md`.

Full production requires:

- the controlled deployment to be healthy;
- runtime identity and migration to match the published manifest;
- queue, database pool, storage and backup readiness;
- candidate-bound HTTPS E2E evidence;
- capability-specific credentials and evidence for WebChat AI, LiveKit/SIP, outbound or Operations;
- `production_authorized=true` from the canonical readiness authority.

No UI action, environment flag or model output can override a failed readiness collector.

## Rollback

Retain the previous immutable image digest, environment snapshot, database backup, uploads backup marker and reverse-proxy configuration.

If the controlled deployment or a later activation degrades:

1. restore `PROVIDER_RUNTIME_KILL_SWITCH=true`;
2. disable affected external capability flags;
3. reapply the controlled profile;
4. execute the canonical rollback script with the previous immutable digest and matching environment snapshot;
5. preserve failure evidence before any retry.
