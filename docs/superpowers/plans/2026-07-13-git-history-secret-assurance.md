# Reachable Git History Secret Assurance — Implementation Plan

> Required execution mode: Superpowers planning, TDD, systematic debugging and verification-before-completion.

**Goal:** Produce complete bounded evidence for credential-shaped material in every reachable Git Blob and historical path alias without mutating repository history or exposing matched values.

**Work Item:** #565

**Baseline:** `main@e96dac837b4f02a297602259953d7c274b9c2063`

**Historical evidence:** PR #603 only; do not revive, merge or cherry-pick it.

## Global boundaries

- No credential rotation or revocation.
- No history rewrite, force-push, branch/tag deletion or visibility change.
- No deployment, Provider, outbound, database or production-data action.
- No raw matched values, source lines, paths, commit messages, author identities, emails or ref names in evidence.
- A finding or incomplete result blocks merge.

## Task 1 — Establish RED on current main

**File:** `scripts/security/tests/test_scan_git_history.py`

- Add tests for removed-secret detection and raw-value/path absence.
- Add logical cross-Blob deduplication.
- Add independent allowlist decisions for identical Blob content at multiple paths.
- Add direct Tree-tag alias coverage.
- Add whitespace-distinct path coverage.
- Add complete same-rule matching when one line contains multiple credentials.
- Add fail-closed coverage for oversized binary-looking suffixes.
- Add complete counting beyond 200 while preserving the tree cap.
- Add shallow rejection and complete Blob accounting.
- Add bounded failure-report behavior.
- Publish Draft PR and prove the tests fail before implementation.

## Task 2 — Implement the history scanner

**File:** `scripts/security/scan_git_history.py`

- Detect Git object format and validate object IDs.
- Reject shallow repositories.
- Enumerate all reachable objects.
- Resolve type/size with batch metadata.
- Enumerate every `(Blob, path)` pair from unique reachable commit root Trees.
- Peel validated branch, remote and tag object IDs to Trees when possible and include those roots.
- Preserve decoded path whitespace exactly.
- Stream each eligible unique Blob once and evaluate every path alias independently.
- Reuse current scanner patterns, placeholders and fingerprints.
- Iterate every match for every rule on each line.
- Deduplicate logical findings without Blob SHA while keeping path in the identity.
- Apply exact unexpired allowlist entries independently per path.
- Treat every Blob above the configured ceiling as unscanned/incomplete regardless of suffix.
- Store at most 100 findings while counting all findings.
- Emit bounded safe pass/fail reports with path digests only.

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
- Enforce tests, scan, Blob accounting, Blob-path enumeration and artifact-scan success.

## Task 4 — Document operations and authority

**Files:**

- `docs/security/git-history-secret-assurance.md`
- `docs/superpowers/specs/2026-07-13-git-history-secret-assurance-design.md`
- `docs/superpowers/plans/2026-07-13-git-history-secret-assurance.md`

- Document pass, finding and incomplete states.
- Document strict path-specific allowlist use.
- Document commit-root and ref-peeled Tree coverage.
- Document whitespace-preserving path identity.
- Document all-match and strict oversized semantics.
- Document separate owner authority for credential and history remediation.
- Document rollback.

## Task 5 — Exact-head verification

- Run all current security tests.
- Prove one allowlisted fixture path does not suppress an identical non-fixture alias.
- Prove a direct Tree-targeting tag contributes its aliases.
- Prove whitespace-distinct paths remain independent.
- Prove all same-rule credentials on one line are counted.
- Prove an oversized `.png`/`.zip`-style path remains incomplete unless bytes are actually scanned.
- Run the real non-shallow reachable-history scan.
- Verify `accounted_blob_count == reachable_blob_count`.
- Verify `reachable_blob_path_count` is present and positive.
- Verify report size is at most 64 KiB.
- Verify the generated report passes artifact scanning.
- Inspect only bounded counts/fingerprints; never publish raw matches or paths.
- Run repository-required checks and CodeQL.
- Request independent review and resolve all actionable threads.
- Re-read latest main and require `0 behind` before merge.

## Decision boundary

- **Clean and complete:** mark Ready and merge with expected Head.
- **Findings:** keep Draft; publish only bounded counts and request separately authorized credential triage.
- **Incomplete:** keep Draft; fix scanner completeness before any security claim.
