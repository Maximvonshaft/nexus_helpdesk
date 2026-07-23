# Controlled Candidate Convergence

## Status and authority

This runbook defines the only repository-side path that turns an accepted `main`
commit into an immutable controlled-server candidate. It publishes and verifies a
candidate image; it does **not** deploy the image or authorize customer traffic.

The canonical authorities are:

- `.github/workflows/canonical-acceptance.yml` for software acceptance;
- `.github/workflows/controlled-candidate-dispatch-bridge.yml` for the bounded
  exact-main dispatch request;
- `.github/workflows/controlled-candidate-convergence.yml` for one-build image
  publication, pull-back verification, provenance and recovery evidence;
- `.github/controlled-candidate-request.json` for the auditable dispatch intent;
- `scripts/release/` for RC, image assurance, publication and final Manifest;
- `scripts/deploy/validate_controlled_server_preflight.py` and
  `deploy/docker-compose.controlled.yml` for target-host admission;
- `docs/runbooks/production-activation.md` for Provider and customer-facing
  capability activation.

No PR comment, model response, UI action or environment toggle can replace these
authorities.

## 1. Accept one exact `main`

Canonical Acceptance must pass on the exact source commit. It verifies backend,
PostgreSQL migrations, frontend and Playwright, static authority, image startup,
Trivy, SBOM, secret history, SAST, dependency closure and CodeQL.

Record:

```text
source_sha
source_tree_sha
frontend_build_sha
migration_revision
```

Any source, tree, lockfile or migration change invalidates prior evidence.

## 2. Dispatch through the bounded request

Update `.github/controlled-candidate-request.json` in the same reviewed change
that restores or updates the release authority. The request must:

- target `main`;
- bind `base_sha` to the immediate pre-merge `main` parent;
- keep `deployment_authorized=false`;
- keep `production_authority=false`;
- keep `external_actions_authorized=false`;
- keep `issue_533=NO_GO`.

After the squash merge, the dispatch bridge verifies the exact push, request
schema, parent SHA and no-go flags. It then dispatches only
`controlled-candidate-convergence.yml` and records the numeric Run ID in Issue
`#724`.

The bridge cannot build, push, attest or deploy anything itself.

## 3. Build, assure and publish one binary

The controlled-candidate workflow runs only on `main` by `workflow_dispatch`.
It:

1. verifies that the checked-out SHA is still the current `origin/main`;
2. executes the existing one-build RC chain and browser smoke tests;
3. reuses that exact image for runtime import checks, Trivy and CycloneDX;
4. enforces zero critical/high and unresolved license findings;
5. publishes the assured image to GHCR as `controlled-<source_sha>`;
6. pulls the immutable digest back and requires the image ID and embedded build
   identity to match the local binary;
7. runs PostgreSQL backup/restore recovery qualification in a disposable service;
8. creates GitHub build provenance for the exact registry digest;
9. emits `controlled-candidate-manifest.json` plus bounded supporting evidence.

Publication occurs only after RC and image assurance pass. No second image build
or mutable `latest` tag is permitted.

## 4. Candidate output

The final artifact must bind:

```text
source_sha
frontend_build_sha
migration_revision
build_time
app_version
registry_image
registry_digest
registry_reference
local_image_id
registry_pull_image_id
provenance_attestation
recovery_evidence
```

The final decision is `CONTROLLED_SERVER_CANDIDATE_PUBLISHED`. Its safety section
must continue to state:

```text
production_ready=false
deployment_performed=false
external_effects_authorized=false
```

A published candidate is deployable to a controlled server but is not full
production authorization.

## 5. Prepare the target server

Create the real controlled environment outside Git from
`deploy/.env.controlled.example` or the local PostgreSQL example. Populate:

- the immutable GHCR digest from the candidate Manifest;
- exact source/frontend SHA and migration head;
- six distinct PostgreSQL service identities;
- JWT, runtime-contract and metrics secrets;
- approved domains and trusted proxies;
- uploads and independently verified backup paths.

Provider, WebChat AI, Voice, outbound, WhatsApp, Speedaf writes and Operations
must remain disabled during controlled deployment.

Run the target-host preflight against the candidate Manifest. It rejects mutable
images, placeholders, duplicate database users, identity mismatch, invalid host
paths and enabled external effects.

## 6. Controlled deployment and acceptance

A separately authorized operator may then apply the controlled Compose topology.
Acceptance must prove:

- image/source/frontend/migration identity equality;
- App and dedicated Worker health;
- queue and database-pool health;
- migration and recovery evidence;
- fresh local-storage equality backup or approved remote storage;
- zero Provider, AI, Voice, outbound, WhatsApp, Speedaf and Operations effects.

## 7. Customer-facing activation

Customer traffic is governed by `docs/runbooks/production-activation.md`.
Provider Canary remains bounded and customer AI stays disabled. Full activation
requires candidate-bound HTTPS E2E evidence and the live readiness endpoint to
return `production_authorized=true`. LiveKit/SIP, SMTP, WhatsApp and operational
write capabilities require their own real credentials and capability-specific
E2E evidence.

## 8. Failure and rollback

A failed RC, image assurance, registry pull-back, recovery test, attestation or
artifact scan blocks candidate publication. Preserve bounded failure evidence;
do not weaken the gate or publish a replacement binary outside this workflow.

For a deployed candidate, retain the prior immutable digest, environment
snapshot, database backup and uploads backup. Rollback restores the kill switch,
disables affected capabilities and uses the canonical rollback script. Never use
an unbounded Docker prune.
