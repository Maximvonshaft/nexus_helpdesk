# Nexus controlled candidate publication

## Single workflow authority

Nexus has exactly one GitHub Actions workflow:

```text
.github/workflows/canonical-acceptance.yml
```

The workflow contains two sequential phases:

1. **Canonical Acceptance** for pull requests, `main` pushes and manual verification.
2. **Controlled candidate publication**, executed only for a `push` to `main` after `required-gate` succeeds.

No second workflow, dispatch bridge, issue-comment trigger or request-file transport is permitted.

## Acceptance phase

Every candidate is frozen by exact source SHA and tree SHA. The required gate depends on:

- candidate identity;
- static authority and residue checks;
- Backend Full;
- PostgreSQL migration rehearsal and acceptance;
- frontend architecture, lint, types, units, build and Playwright;
- exact-Head image build, Trivy, SBOM, migration, startup and health;
- secret history, SAST, dependency assurance and CodeQL.

Pull requests and manual runs stop after acceptance. They never publish an image.

## Main-only publication phase

For a `push` to `refs/heads/main`, and only after `required-gate=success`, the same workflow starts three bounded jobs:

1. `controlled-build-publish`
2. `controlled-recovery`
3. `controlled-bind-attest`

The publication jobs verify that the accepted source is still the exact current `origin/main`. A newer `main` commit cancels the stale workflow through the canonical concurrency group.

### Controlled build and publication

The job reuses the repository's one-build RC authority:

```text
scripts/release/run_controlled_rc_gate.sh
```

It then:

- checks runtime imports and Gunicorn configuration;
- generates Trivy and CycloneDX evidence for the same RC image;
- executes release-image assurance and compliance binding;
- tags and pushes that exact image to GHCR as `controlled-<source-sha>`;
- pulls it back by digest;
- requires local and registry-pulled image IDs to match;
- emits a bounded registry publication receipt.

Failure evidence is separately bounded and scanned. A failed RC or image-assurance step cannot reach publication.

### Recovery qualification

The recovery job uses an isolated PostgreSQL service and the existing recovery authority:

```text
scripts/release/run_controlled_recovery_gate.sh
```

It proves backup/restore behavior, migration identity, foreign-key validity, the synthetic restore marker, and bounded RPO/RTO evidence without production data.

### Provenance and final manifest

After both jobs succeed, the final job:

- resolves the immutable GHCR digest;
- creates GitHub build-provenance attestation for that digest;
- binds the RC manifest, image assurance, compliance binding, registry receipt and recovery evidence;
- generates `controlled-candidate-manifest.json`;
- scans all final artifacts;
- uploads `controlled-candidate-<source-sha>`;
- records the exact source, image digest, migration and workflow Run ID in governance Issue #724.

## Safety boundary

A published controlled candidate always remains:

```text
production_ready=false
deployment_performed=false
external_effects_authorized=false
```

The publication phase does not deploy a server and does not enable Provider traffic, WebChat AI, Voice, outbound dispatch, WhatsApp, Speedaf writes or Operations Dispatch.

## Target-server deployment

After publication, obtain the final manifest artifact and populate `deploy/.env.controlled` with its exact values:

```text
CONTROLLED_IMAGE=<registry image@sha256 digest>
IMAGE_TAG=<same immutable digest>
GIT_SHA=<manifest source sha>
FRONTEND_BUILD_SHA=<same source sha>
EXPECTED_MIGRATION_HEAD=<manifest migration revision>
BUILD_TIME=<manifest build time>
APP_VERSION=<manifest app version>
```

Supply real process-specific PostgreSQL accounts, runtime secrets, origins, metrics token, storage paths and backup marker. Credentials must never be committed.

Run the controlled server preflight:

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

Only a separately authorized operator may start the controlled Compose topology.

## Customer-facing activation

Customer-facing activation is governed by `docs/runbooks/production-activation.md` and requires:

- a healthy controlled deployment;
- runtime identity and Alembic head matching the published manifest;
- queue, database pool, storage and backup readiness;
- exact-candidate HTTPS E2E evidence;
- capability-specific credentials and evidence;
- `production_authorized=true` from the canonical readiness authority.

No UI action, environment flag or model output can override a failed readiness collector.
