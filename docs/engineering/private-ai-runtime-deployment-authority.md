# Private AI Runtime deployment authority

## Status

This document defines the first repository-owned deployment contract for #591. It is an additive, non-deploying foundation. Production remains `NO_GO` until the complete Work Item and exact-candidate release evidence are accepted.

## Verified repository gap

The Runtime integration and operational runbooks exist, but current main does not contain an isolated source-of-truth package capable of rebuilding the deployed Private AI Runtime. Historical Runtime state includes server-local scripts and mutable assumptions. A clean replacement host therefore cannot be proven from one reviewed commit and a bounded secret set.

The gap is distinct from:

- #586, which defines Runtime capability identity and compatibility;
- #582, which governs Provider traffic and canary selection;
- #569, which governs Nexus release images and provenance;
- #532, which governs broader recovery and data-lifecycle evidence;
- #533, which owns final GO/NO-GO.

## Decision

Use `infra/private-ai-runtime/` as the isolated repository boundary until a separately governed dedicated Runtime repository is approved. The boundary will contain only reviewed non-secret deployment source and contracts. It must have an independently identifiable manifest even when stored in the Nexus repository.

The first contract is `nexus.private_ai_runtime.deployment_manifest.v1`.

### Exact identities

A candidate manifest binds:

- repository, commit SHA and tree SHA;
- the exact #586 capability-contract path and SHA-256;
- immutable release-root artifacts, file modes and SHA-256 values;
- digest-pinned container images;
- model capability, model identifier, revision and artifact SHA-256;
- service manager, definition path and definition SHA-256;
- rollback package identity.

Branches, tags, mutable container tags, model aliases without revisions and server-local file names are not sufficient identities.

### Immutable and mutable state

Immutable release files live under a release-specific root. Mutable paths cannot be located under that root.

Runtime mutable state is explicitly non-authoritative. This includes model caches, generated reports and Qdrant derived indexes. PostgreSQL Knowledge remains the source of truth. Any future backup policy may require selected mutable paths to be backed up, but backup need does not convert them into operational truth.

### Secrets

The manifest stores references only:

- absolute root-managed file paths outside the immutable release; or
- approved secret-store URIs.

Inline secret-shaped fields and token/password/API-key command arguments fail validation. Validation findings never echo field values, secret references or argv content.

### Acceptance and rollback

Acceptance and rollback commands are represented as argv arrays. The validator never executes them. A separate reviewed runner must later provide execution policy, environment controls, timeouts, evidence bounds and external-effect authorization.

A candidate must declare checks for:

- GPU placement;
- generation hot path;
- retrieval;
- voice;
- metrics;
- model identity.

Rollback is non-destructive and targets a different exact release. A valid manifest does not prove rollback success; #591 still requires an isolated-host rehearsal.

### Drift

A candidate declares exact paths to observe, a bounded interval and a mutable result path. Drift must fail closed. The current slice validates that contract but does not install a timer or monitor a host.

## Validator behavior

`scripts/ci/check_private_ai_runtime_deployment_manifest.py` uses only the Python standard library. It performs a bounded read of at most 1 MiB, strict key validation, normalized POSIX path validation, hash and digest checks, cross-field consistency checks and bounded result generation.

Result schema: `nexus.private_ai_runtime.deployment_manifest_validation.v1`.

The result exposes:

- `ok`;
- canonical manifest SHA-256 when serializable;
- source commit SHA only after the complete manifest validates;
- finding count;
- bounded `{code, path}` findings.

It does not expose source values from invalid manifests.

## Remaining #591 closure work

This slice intentionally leaves the following open:

1. inventory the active A10 Runtime without publishing secrets or internal topology;
2. decide whether the long-term owner is this isolated package or a dedicated repository;
3. version service code, systemd/compose definitions, model manifests, Qdrant configuration, monitoring and deployment automation;
4. create one real exact deployment manifest from reviewed assets;
5. implement idempotent host preflight, install, acceptance, backup, restore, rollback and drift runners;
6. rebuild on a clean isolated host;
7. verify generation, retrieval, voice, observability, backup/restore and rollback;
8. bind evidence into #532/#533 and independently review it.

No live-host action, deployment, Provider enablement, traffic switch, model replacement, production-data mutation or destructive migration is authorized by this document.
