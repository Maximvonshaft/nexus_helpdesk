# Reachable Git History Secret Assurance — Implementation Plan

> Required execution mode: Superpowers planning, TDD, systematic debugging and verification-before-completion.

**Goal:** Produce complete bounded evidence for credential-shaped material in every reachable Git Blob without mutating repository history or exposing matched values.

**Work Item:** #565

**Baseline:** `main@e96dac837b4f02a297602259953d7c274b9c2063`

**Historical evidence:** PR #603 only; do not revive, merge or cherry-pick it.

## Global boundaries

- No credential rotation or revocation.
- No history rewrite, force-push, branch/tag deletion or visibility change.
- No deployment, Provider, outbound, database or production-data action.
- No raw matched values, source lines, commit messages, author identities, emails or ref names in evidence.
- A finding or incomplete result blocks merge.

## Task 1 — Establish RED on current main

**File:** `scripts/security/tests/test_scan_git_history.py`

- Add tests for removed-secret detection and raw-value absence.
- Add logical cross-Blob deduplication.
- Add complete counting beyond 200 while preserving the tree cap.
- Add shallow rejection and complete Blob accounting.
- Add bounded failure-report behavior.
- Publish Draft PR and prove Security Assurance fails at missing implementation.

## Task 2 — Implement the history scanner

**File:** `scripts/security/scan_git_history.py`

- Detect Git object format and validate object IDs.
- Reject shallow repositories.
- Enumerate all reachable objects.
- Resolve type/size with batch metadata.
- Stream eligible unique Blobs once.
- Reuse current scanner patterns, placeholders and fingerprints.
- Deduplicate logical findings without Blob SHA.
- Apply exact unexpired allowlist entries.
- Account for every Blob and fail closed on unknown oversized content.
- Store at most 100 findings while counting all findings.
- Emit bounded safe pass/fail reports.

## Task 3 — Add reliable CI evidence

**File:** `.github/workflows/git-history-secret-assurance.yml`

- Use immutable Action SHAs and read-only permission.
- Checkout the exact PR Head with full history and tags.
- Compile and run all security tests.
- Run the complete history scan only after tests pass.
- Always create a bounded history report.
- Scan generated evidence.
- Always create numeric exit-status evidence.
- Upload only bounded JSON files.
- Enforce tests, scan, completeness, accounting and artifact-scan success.

## Task 4 — Document operations and authority

**Files:**

- `docs/security/git-history-secret-assurance.md`
- `docs/superpowers/specs/2026-07-13-git-history-secret-assurance-design.md`
- `docs/superpowers/plans/2026-07-13-git-history-secret-assurance.md`

- Document pass, finding and incomplete states.
- Document strict allowlist use.
- Document separate owner authority for credential and history remediation.
- Document rollback.

## Task 5 — Exact-head verification

- Run all current security tests.
- Run the real non-shallow reachable-history scan.
- Verify `accounted_blob_count == reachable_blob_count`.
- Verify report size is at most 64 KiB.
- Verify the generated report passes artifact scanning.
- Inspect only bounded counts/fingerprints; never publish raw matches.
- Run repository-required checks.
- Request independent review and resolve all actionable threads.
- Re-read latest main and require `0 behind` before merge.

## Decision boundary

- **Clean and complete:** mark Ready and merge with expected Head.
- **Findings:** keep Draft; publish only bounded counts and request separately authorized credential triage.
- **Incomplete:** keep Draft; fix scanner completeness before any security claim.
