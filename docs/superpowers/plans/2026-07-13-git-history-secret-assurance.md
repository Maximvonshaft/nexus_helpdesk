# Reachable Git History Secret Assurance — Implementation Plan

> Required execution mode: Superpowers planning, TDD, systematic debugging and verification-before-completion.

**Goal:** Produce complete bounded evidence for credential-shaped material in every reachable Git Blob, historical path alias and credential occurrence without mutating repository history or exposing matched values.

**Work Item:** #565

**Historical evidence:** PR #603 only; do not revive, merge or cherry-pick it.

## Global boundaries

- No credential rotation or revocation.
- No history rewrite, force-push, branch/tag deletion or visibility change.
- No deployment, Provider, outbound, database or production-data action.
- No raw matched values, source lines, paths, unsafe suffixes, commit messages, author identities, emails or ref names in evidence.
- No PR Head scanner, allowlist, workflow, dependency or script may execute in the pull-request assurance job.
- A finding, incomplete result, failed artifact scan or failed CodeQL result blocks closure.

## Task 1 — Establish adversarial RED contracts

**Files:**

- `scripts/security/tests/test_scan_git_history.py`
- `scripts/security/tests/test_scan_git_history_hardening.py`

Prove failure before each implementation for:

- credentials removed from HEAD;
- changed-Blob logical deduplication;
- identical content under multiple paths;
- direct Tree-targeting tags;
- whitespace-distinct paths and exact allowlist parsing;
- multiple different and repeated identical same-rule values on one line;
- complete history counts beyond 200 while the tree cap stays unchanged;
- oversized binary-looking paths;
- credential-shaped path suffixes;
- shallow and malformed repositories;
- missing reports and tainted evidence upload ordering;
- execution of PR-controlled scanner/policy rather than trusted base code.

## Task 2 — Implement complete history scanning

**Files:**

- `scripts/security/scanner.py`
- `scripts/security/scan_git_history.py`

- Preserve exact allowlist path strings; reject only unsafe path forms.
- Detect Git object format and reject shallow repositories.
- Enumerate all reachable objects and object metadata.
- Enumerate every `(Blob, path)` pair from commit root Trees and ref-peeled Trees.
- Preserve decoded path whitespace exactly.
- Read each eligible Blob once and evaluate every alias independently.
- Reuse current patterns and placeholder policy.
- Use all regex matches per line.
- Preserve the first occurrence's base fingerprint and derive deterministic fingerprints for later identical occurrences.
- Deduplicate logical findings without Blob SHA while retaining path and occurrence identity.
- Apply exact unexpired allowlist entries per path and occurrence.
- Treat every Blob over the ceiling as incomplete regardless of suffix.
- Emit path digests and only safe bounded suffixes.
- Store at most 100 findings while counting all findings.

## Task 3 — Establish trusted CI execution

**File:** `.github/workflows/git-history-secret-assurance.yml`

- Use `pull_request_target`, immutable Action SHAs and read-only permissions.
- Checkout trusted base code/policy into `.trusted`.
- Checkout exact PR Head full history into `target` with credentials not persisted.
- Verify exact target SHA and non-shallow state.
- Compile and run only `.trusted` security tests.
- Run `.trusted` history and artifact scanners against `target` data.
- Never execute target Python, scripts, workflows, dependencies or allowlist.
- Always create bounded scan and numeric status reports.
- Run artifact scanning before full evidence upload.
- Upload the complete report set only when artifact scan exit is zero.
- On artifact-scan failure, upload only sanitized numeric status.
- Enforce tests, history exit, completeness, Blob accounting and clean artifact status.

## Task 4 — Document operations and authority

**Files:**

- `docs/security/git-history-secret-assurance.md`
- `docs/superpowers/specs/2026-07-13-git-history-secret-assurance-design.md`
- `docs/superpowers/plans/2026-07-13-git-history-secret-assurance.md`

Document:

- trusted base versus untrusted target execution;
- bootstrap and post-merge assurance boundary;
- pass, finding and incomplete states;
- exact untrimmed path/occurrence allowlist semantics;
- commit-root and ref-peeled Tree coverage;
- all-match and oversized fail-closed behavior;
- safe suffix and pre-upload evidence validation;
- separate owner authority for credential/history remediation;
- rollback.

## Task 5 — Bootstrap PR verification

The bootstrap PR must prove on one exact Head:

- all current and focused security tests pass through existing trusted repository CI;
- dual-checkout workflow text prevents target-code execution;
- current-tree scan and dependency/SBOM gates pass;
- Python and JavaScript CodeQL pass;
- Backend, Migration, Readiness, Release Image, Webapp, Smoke, Integration, WebCall and coordination checks pass;
- independent review reports no remaining Critical/Important issue;
- latest `main` is unchanged or safely synchronized and the branch is `0 behind`;
- merge uses expected Head.

The PR must not claim authoritative tamper-resistant history assurance from a workflow definition that is not yet on the trusted base branch.

## Task 6 — Post-merge trusted assurance

After the bootstrap merge, the trusted `main` push run must prove:

- trusted tests execute successfully;
- the real non-shallow Nexus history scan completes;
- one fixture allowlist cannot suppress another Blob alias;
- direct Tree tags and whitespace paths remain independent;
- all distinct and repeated identical values on one line are counted;
- oversized `.png`/`.zip`-style paths remain incomplete unless actually scanned;
- credential-shaped suffixes are not emitted;
- an artifact-scan failure cannot upload the history report;
- `accounted_blob_count == reachable_blob_count`;
- `reachable_blob_path_count` is positive and bounded;
- report size is at most 64 KiB;
- clean evidence passes artifact scanning before full upload;
- unsuppressed finding count is zero.

Only after this trusted post-merge run passes may #565's scanner/evidence acceptance be closed.

## Decision boundary

- **Bootstrap checks pass:** merge infrastructure with expected Head, then wait for trusted main push assurance.
- **Post-merge clean and complete:** close scanner/evidence acceptance.
- **Findings:** keep #565 open; publish only bounded counts and request separately authorized credential triage.
- **Incomplete or tainted evidence:** keep #565 open; correct coverage or evidence handling before any assurance claim.
