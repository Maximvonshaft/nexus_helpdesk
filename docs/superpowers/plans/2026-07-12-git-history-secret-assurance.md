# Git History Secret Assurance — Implementation Plan

> **Required workflow:** execute task-by-task with test-driven-development, specification review, code-quality review and verification-before-completion.

**Goal:** Scan every unique regular Git blob reachable from all branches and tags for credential-shaped material removed from the current tree, and emit bounded redacted evidence.

**Starting main:** `6faeb50a65b64f84816833a924d3793a498eff96`

**Work Item:** #565 (`NEX-AUD-001`), non-destructive full-history evidence slice only.

**Contract:** `nexus_security_git_history_scan_v1`

**Tech stack:** Python 3.11 standard library, Git plumbing, `unittest`, GitHub Actions.

## Global constraints

- No repository visibility change.
- No credential rotation, revocation or secret-manager mutation.
- No Git-history rewrite, force-push or branch/tag deletion.
- No release-image, deployment, Provider, outbound, database or production-data action.
- No raw matched value, source line, author, email, ref name or commit message in evidence.
- Workflow dependencies must use immutable SHAs.
- A finding or incomplete scan fails the check; the PR remains draft until evidence is clean or separately authorized remediation is completed.

---

## Task 1 — Establish shared detector behavior with RED tests

**Files:**
- Modify: `scripts/security/scanner.py`
- Create: `scripts/security/tests/test_scan_git_history.py`

- [ ] Create focused tests importing `scan_git_history.py` by exact path.
- [ ] Add a test proving a secret committed in revision 1 and deleted in revision 2 is still detected.
- [ ] Add a test proving placeholder-shaped values are ignored.
- [ ] Add a test proving the raw matched token never appears in serialized evidence.
- [ ] Add a test proving unchanged/reused blobs do not create duplicate findings.
- [ ] Run the test module and record the expected missing-module/missing-function RED failure.
- [ ] Commit tests before production implementation.

## Task 2 — Extract the shared text detector

**Files:**
- Modify: `scripts/security/scanner.py`
- Test: existing security tests plus `test_scan_git_history.py`

- [ ] Add `scan_secret_text(relative_path, text) -> list[Finding]`.
- [ ] Move existing line/placeholder/pattern logic into the function without changing fingerprints or finding shape.
- [ ] Refactor `scan_secret_files` to delegate to `scan_secret_text`.
- [ ] Run existing scanner tests and focused new tests.
- [ ] Commit the behavior-preserving extraction.

## Task 3 — Implement strict Git object enumeration

**Files:**
- Create: `scripts/security/scan_git_history.py`
- Test: `scripts/security/tests/test_scan_git_history.py`

- [ ] Implement strict parsing for `git rev-list --objects --all` output.
- [ ] Resolve type and size through `git cat-file --batch-check`.
- [ ] Validate SHA-1 object IDs, object counts and batch response order.
- [ ] Retain only unique blob objects with deterministic bounded paths.
- [ ] Compute commit count, current source SHA and reference-set SHA-256 without emitting raw ref names.
- [ ] Add malformed-output and command-failure tests.
- [ ] Commit enumeration logic.

## Task 4 — Implement streaming blob scan and completeness policy

**Files:**
- Modify: `scripts/security/scan_git_history.py`
- Test: `scripts/security/tests/test_scan_git_history.py`

- [ ] Stream eligible blobs through `git cat-file --batch`.
- [ ] Read exact declared byte lengths and reject truncated/mismatched records.
- [ ] Decode UTF-8 text only; count NUL-bearing or undecodable content as binary/unreadable.
- [ ] Skip oversized blobs only when their suffix is on the explicit binary list.
- [ ] Mark unknown/text-like oversized blobs as incomplete and failing.
- [ ] Scan decoded text with `scan_secret_text`.
- [ ] Deduplicate findings by path/rule/line/fingerprint.
- [ ] Add tests for known binary oversized and unknown oversized behavior.
- [ ] Commit scanning logic.

## Task 5 — Add allowlist and bounded report

**Files:**
- Modify: `scripts/security/scan_git_history.py`
- Test: `scripts/security/tests/test_scan_git_history.py`

- [ ] Reuse the existing strict allowlist loader and exact path/rule/fingerprint key.
- [ ] Suppress only exact, non-expired entries.
- [ ] Count all unsuppressed findings while storing at most 100 records.
- [ ] Emit schema, status, completeness, source/ref digests, object/blob scan counts, counts by rule, suppressed count and bounded finding metadata.
- [ ] Use `blob_sha`, not raw object content or commit metadata.
- [ ] Enforce the existing 64 KiB report limit.
- [ ] Return non-zero for findings or incomplete coverage.
- [ ] Add allowlist, truncation and serialization tests.
- [ ] Commit report/CLI logic.

## Task 6 — Add immutable read-only CI and engineering guide

**Files:**
- Create: `.github/workflows/git-history-secret-assurance.yml`
- Create: `docs/security/git-history-secret-assurance.md`

- [ ] Run for every PR to main, every main push and manual dispatch.
- [ ] Use immutable checkout/setup/upload Action SHAs.
- [ ] Checkout with `fetch-depth: 0` and `persist-credentials: false`.
- [ ] Compile modules and run focused plus existing security tests.
- [ ] Run the full-history scanner while preserving its exit status and report.
- [ ] Pass the report through `scripts/security/scan_artifacts.py`.
- [ ] Upload only bounded scanner and artifact-validation reports.
- [ ] Exit with the scanner or artifact-policy failure.
- [ ] Document result interpretation, remediation authority and rollback.
- [ ] Commit workflow and documentation.

## Task 7 — Exact-head verification and handoff

- [ ] Run syntax compilation on the exact final head.
- [ ] Run existing security tests and focused history tests.
- [ ] Run the real full-history scan on the exact final head.
- [ ] Inspect the bounded artifact; never copy a raw match into Issue/PR text.
- [ ] Confirm the artifact leak scan passes.
- [ ] Confirm all repository-required workflows are green on the same head.
- [ ] Re-read changed files and prove scope remains within the Claim.
- [ ] Re-read open PR manifests and unresolved review threads.
- [ ] Complete specification and code-quality reviews.
- [ ] If the real scan passes, mark the PR ready for review and move #565 to In Review.
- [ ] If findings or incomplete coverage remain, keep the PR draft, publish only bounded counts/fingerprints, and identify the separately authorized remediation required.

## Verification commands

```bash
python -m py_compile scripts/security/scanner.py scripts/security/scan_git_history.py
python -m unittest discover -s scripts/security/tests -p 'test_*.py'
python scripts/security/scan_git_history.py \
  --root . \
  --allowlist config/security/secret-scan-allowlist.json \
  --output artifacts/security-git-history-scan.json
python scripts/security/scan_artifacts.py \
  --root . \
  --output artifacts/security-git-history-report-scan.json \
  artifacts/security-git-history-scan.json
```

## Acceptance boundary

This plan closes only the evidence gap. It does not assert that the repository may be public, that exposed credentials are safe, or that historical findings can be suppressed. Visibility decisions, credential rotation/revocation, artifact removal and history rewriting remain explicit owner-controlled #565 work.