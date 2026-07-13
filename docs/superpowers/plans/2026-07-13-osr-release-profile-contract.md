# OSR Release Profile Core Contract — Implementation Plan

> Required execution mode: Superpowers planning, TDD, systematic debugging and verification-before-completion.

**Goal:** Establish a framework-light, versioned and fail-closed release-profile authority without touching runtime wiring or active dependency paths.

**Work Item:** #549

**Baseline:** `main@df4863d4e4c77b47d694d4eca3f655fa93a8e67b`

## Global boundaries

- Do not modify Settings, `main.py`, API, Worker, Provider, Dispatch, Tenant, migration or deployment files.
- Do not query a database or external system.
- Do not execute Provider/outbound/deploy actions.
- Do not close #549; this is the core authority slice only.
- Preserve exact path ownership declared in the Claim.

## Task 1 — RED profile completeness and evaluation semantics

**File:** `backend/tests/test_nexus_osr_release_profiles.py`

- [ ] Require schema/profile version.
- [ ] Require every profile to declare every capability exactly once.
- [ ] Require Full OSR to mark every capability Required.
- [ ] Require Shadow to forbid external writes.
- [ ] Prove unknown profiles/capabilities/states fail closed.
- [ ] Prove Required, Optional and Forbidden state matrices.
- [ ] Prove `not_ready > degraded > ready` precedence.
- [ ] Prove reason codes are deterministic, unique, sorted and bounded.
- [ ] Run the focused test and record RED before implementation.

## Task 2 — RED safe configuration fingerprint

**File:** `backend/tests/test_nexus_osr_release_profiles.py`

- [ ] Prove mapping-order independence.
- [ ] Prove secret-key value changes do not alter the digest.
- [ ] Prove non-secret value changes alter the digest.
- [ ] Reject excessive depth, entries, list length, string length and unsupported objects.
- [ ] Prove no configuration values are returned.

## Task 3 — Implement the minimal core authority

**File:** `backend/app/services/nexus_osr/release_profiles.py`

- [ ] Add fixed enums and immutable dataclasses.
- [ ] Add complete profile registry.
- [ ] Validate registry at import/construction.
- [ ] Implement deterministic fail-closed evaluation.
- [ ] Implement bounded key-aware configuration normalization and SHA-256.
- [ ] Keep the module standard-library only and free of I/O.
- [ ] Run focused tests and Python compilation.

## Task 4 — Add focused CI gate and engineering guide

**Files:**

- `.github/workflows/osr-release-profile-contract.yml`
- `docs/engineering/osr-release-profile-contract.md`

- [ ] Pin immutable Actions.
- [ ] Use read-only permissions.
- [ ] Compile and run only the focused contract tests.
- [ ] Run `git diff --check` on PRs.
- [ ] Document profile semantics, consumer rules and non-authority boundaries.

## Task 5 — Exact-head verification and delivery

- [ ] Compare against latest main and require only six claimed paths.
- [ ] Re-read open PR manifests for conflict.
- [ ] Run all repository-required exact-head checks.
- [ ] Request independent review and resolve actionable threads.
- [ ] Require `0 behind` before merge.
- [ ] Merge with expected Head SHA.
- [ ] Update #549 with accepted evidence while leaving it open for collectors/runtime integration.

## Decision boundary

- **Pass:** merge the additive core contract and keep #549 In Progress.
- **Contract defect:** fix with a new RED regression before implementation.
- **Path conflict or main movement:** stop, reconcile and revalidate on current main.
- **Dependency expansion:** do not absorb it; create or use the owning follow-up slice.
