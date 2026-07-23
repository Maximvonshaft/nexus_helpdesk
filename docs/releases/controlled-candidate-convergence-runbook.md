# Controlled Candidate Convergence

## Status and authority

Nexus has one GitHub Actions authority:

```text
.github/workflows/canonical-acceptance.yml
```

That workflow performs two ordered responsibilities:

1. accept an exact software candidate on pull requests, `main` pushes and manual runs;
2. only on exact `main`, and only after `required-gate` succeeds, publish a controlled-server candidate.

The publication phase does **not** deploy the image and does not authorize Provider,
WebChat AI, Voice, outbound, WhatsApp, Speedaf or Operations effects.

The supporting authorities are:

- `scripts/release/` for RC, image assurance, registry publication, recovery binding
  and final Candidate Manifest generation;
- `scripts/deploy/validate_controlled_server_preflight.py` for target-host admission;
- `deploy/docker-compose.controlled.yml` and its PostgreSQL overlay;
- `docs/runbooks/production-activation.md` for customer-facing activation;
- Issue `#724` as trace metadata for published controlled candidates.

No second workflow, PR comment, model response, UI action or mutable image tag may
replace these authorities.

## 1. Exact software acceptance

The first phase freezes the event Head and proves:

```text
source_sha
source_tree_sha
frontend_build_sha
migration_revision
```

It then runs:

- repository and authority verification;
- full backend regression;
- PostgreSQL upgrade, rollback and re-upgrade;
- frontend architecture, lint, types, tests, build and Playwright;
- exact-Head image build, Trivy, SBOM, migration, startup and health;
- secret-history scan, SAST, dependency audits and CodeQL;
- `required-gate`, which requires every canonical job to succeed.

Any source, tree, lockfile or migration change invalidates prior evidence.

## 2. Main-only controlled candidate publication

The controlled release jobs run only when:

- the event is a `main` push or a manual dispatch on `main`;
- the checked-out SHA still equals current `origin/main`;
- `required-gate` succeeded for the same exact SHA.

A pull request can verify the release contracts but cannot publish an image.

## 3. Build, assure and publish one release binary

The release phase:

1. executes the existing one-build RC chain and browser smoke tests;
2. reuses the resulting RC image for runtime import checks, Trivy and CycloneDX;
3. rejects critical/high vulnerabilities and unresolved license evidence;
4. publishes only the assured image to GHCR as `controlled-<source_sha>`;
5. resolves the immutable registry digest;
6. removes the mutable tag locally, pulls the digest reference back and requires the
   local and pulled image IDs plus embedded identity metadata to match.

A failed RC or image assurance produces only bounded failure evidence and blocks
publication. The workflow does not build a replacement binary outside the canonical
RC chain.

## 4. Recovery and provenance

In parallel with image assurance, a disposable PostgreSQL service runs the canonical
backup/restore recovery qualification. It proves migration identity, foreign-key
validation, synthetic marker restoration and bounded RPO/RTO evidence without
production data.

After both publication and recovery pass, the workflow:

- creates GitHub build provenance for the exact GHCR digest;
- binds source, frontend, migration, build metadata, local image ID, pulled image ID,
  registry digest, assurance, recovery and attestation;
- emits `controlled-candidate-manifest.json`;
- scans all final JSON evidence for secrets and unbounded payloads;
- uploads a `controlled-candidate-<source_sha>` artifact;
- records source, image, migration and Run ID in Issue `#724`.

The trace explicitly records:

```text
production_ready=false
deployment_performed=false
external_effects_authorized=false
```

The resulting decision is a published controlled-server candidate, not full production
authorization.

## 5. Prepare the target server

Create the real controlled environment outside Git from:

```text
deploy/.env.controlled.example
```

or the local PostgreSQL example. Populate:

- the immutable GHCR digest from the Candidate Manifest;
- exact source/frontend SHA and migration head;
- six distinct PostgreSQL service identities;
- JWT, runtime-contract and metrics secrets;
- approved domains and trusted proxies;
- uploads and independently verified backup paths.

Provider, WebChat AI, Voice, outbound, WhatsApp, Speedaf writes and Operations must
remain disabled during controlled deployment.

Run `scripts/deploy/validate_controlled_server_preflight.py` against the exact Candidate
Manifest. It rejects mutable images, placeholders, duplicate database users, identity
mismatch, invalid host paths and enabled external effects.

## 6. Controlled deployment and acceptance

A separately authorized operator may apply the controlled Compose topology after
preflight. Acceptance must prove:

- image/source/frontend/migration equality;
- App and dedicated Worker health;
- queue and database-pool health;
- authenticated `/metrics` succeeds and unauthenticated `/metrics` returns 401;
- fresh local-storage equality backup or approved remote storage;
- migration and recovery evidence;
- zero Provider, AI, Voice, outbound, WhatsApp, Speedaf and Operations effects.

The target server, DNS, TLS, database credentials and backup locations are environmental
facts and must not be fabricated in Git.

## 7. Customer-facing activation

Customer traffic is governed by `docs/runbooks/production-activation.md`.

Provider Canary remains bounded and customer WebChat AI stays disabled. Full activation
requires:

- the live controlled candidate identity;
- candidate-bound HTTPS E2E evidence;
- real Provider and capability credentials;
- capability-specific evidence for WebChat AI, LiveKit/SIP, outbound and Operations;
- the live readiness authority returning `production_authorized=true`.

## 8. Failure and rollback

A failed acceptance, RC, image assurance, registry pull-back, recovery test, attestation,
artifact scan or target-host preflight blocks progression.

Retain the previous immutable image digest, environment snapshot, database backup and
uploads backup. Rollback restores the Provider kill switch, disables affected
capabilities and uses the canonical rollback script. Never use an unbounded Docker
prune.
