# OSR Release Profile Core Contract — Implementation Plan

> Required execution mode: Superpowers planning, TDD, systematic debugging and verification-before-completion.

**Goal:** Establish a framework-light, versioned and fail-closed release-profile authority without touching runtime wiring or active dependency paths.

**Work Item:** #549  
**PR:** #700  
**Delivery status:** Partial Foundation; parent #549 remains open  
**Current reconciliation baseline:** `main@7006af1e88d7681713cfd5ad4b540a3964d780f1`

## Global boundaries

- Do not modify Settings, `main.py`, API, Worker, Provider, Dispatch, Tenant, migration or deployment files.
- Do not query a database or external system.
- Do not execute Provider/outbound/deploy actions.
- Do not close #549; this is the core authority slice only.
- Preserve exact path ownership declared in the Claim.
- Do not merge; final merge, Issue closure and dependency unlock belong to unified acceptance.

## Task 1 — RED profile completeness and evaluation semantics

**File:** `backend/tests/test_nexus_osr_release_profiles.py`

- [x] Require schema/profile version.
- [x] Require every profile to declare every capability exactly once.
- [x] Require Full OSR to mark every capability Required.
- [x] Require Shadow to forbid external writes.
- [x] Prove unknown profiles/capabilities/states fail closed.
- [x] Prove Required, Optional and Forbidden state matrices.
- [x] Prove `not_ready > degraded > ready` precedence.
- [x] Prove reason codes are deterministic, unique, sorted and bounded.
- [x] Record RED before implementation correction.

## Task 2 — RED safe configuration fingerprint

**File:** `backend/tests/test_nexus_osr_release_profiles.py`

- [x] Prove mapping-order independence.
- [x] Prove token/password/API/private/access/signing/secret-key value changes do not alter the digest.
- [x] Prove non-secret and token-count value changes alter the digest.
- [x] Reject excessive depth, mapping entries, sequence length, key/string length, numeric magnitude and unsupported objects.
- [x] Prove invalid values under sensitive keys are validated before redaction and fail closed.
- [x] Prove no configuration values are returned.

## Task 3 — Implement the minimal core authority

**File:** `backend/app/services/nexus_osr/release_profiles.py`

- [x] Add fixed enums and immutable dataclasses.
- [x] Add complete 24-capability profile registry.
- [x] Validate registry at import/construction.
- [x] Implement deterministic fail-closed evaluation.
- [x] Implement bounded key-aware configuration normalization and SHA-256.
- [x] Keep the module standard-library only and free of I/O.
- [x] Run focused tests and Python compilation locally.

## Task 4 — Add focused CI gate and engineering guide

**Files:**

- `.github/workflows/osr-release-profile-contract.yml`
- `docs/engineering/osr-release-profile-contract.md`

- [x] Pin immutable Actions.
- [x] Use read-only permissions.
- [x] Compile and run only the focused contract tests.
- [x] Run `git diff --check` on PRs.
- [x] Document profile semantics, consumer rules and non-authority boundaries.

## Task 5 — Exact-head verification and delivery

- [x] Reconcile with the latest observed main without Force Push.
- [x] Compare actual changed files against the six claimed paths.
- [x] Re-read open PR manifests and target Review Threads for conflict.
- [ ] Require all repository exact-head Backend/Security/Integration/RC checks to finish successfully.
- [ ] Request independent review and resolve all actionable current-head threads.
- [x] Require `0 behind` against the observed main.
- [ ] Mark Ready only after current exact-head verification and review are complete.
- [ ] Update #549 with accepted evidence while leaving it open for collectors/runtime integration.
- [ ] Do not merge; hand off to unified acceptance.

## Decision boundary

- **Pass:** mark PR #700 Ready only when its current exact head has passing required checks and no actionable Review Thread; keep #549 In Progress and do not merge.
- **Contract defect:** add or retain a RED regression, apply the narrow root-cause fix, and rerun exact-head evidence.
- **Path conflict or main movement:** stop, reconcile without Force Push and revalidate on current main.
- **Dependency expansion:** do not absorb it; leave collectors and Runtime integration to the owning follow-up slice under #549.
