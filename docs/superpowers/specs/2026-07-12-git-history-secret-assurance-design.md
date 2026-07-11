# Git History Secret Assurance — Design

## Authority and scope

This design implements one independent, non-destructive slice of Work Item #565 (`NEX-AUD-001`): scan every reachable regular Git blob for credential-shaped material, including content removed from the current tree, and publish bounded redacted evidence.

The user authorized autonomous selection and implementation of unoccupied Nexus Work Items. The #565 Work Item still reserves repository visibility changes, credential rotation/revocation and Git-history rewriting for explicit owner-approved remediation. This slice performs none of those actions.

The workflow follows Superpowers: inspect existing security controls and concurrent PRs, compare approaches, lock a narrow design, write the plan, implement test-first, review, and verify the exact head before declaring readiness.

## Existing control gap

Nexus already has a current-tree secret scanner in `scripts/security/scan_repository.py` and a read-only `security-assurance` workflow. That scanner enumerates `git ls-files`, so it cannot find credentials committed in an earlier revision and later removed.

PR #580 separately owns release-image vulnerability, SBOM, license and artifact assurance. Reusing or extending that PR would mix source-history credential evidence with image-release policy and create an avoidable conflict.

The remaining gap is reachable Git history:

- branches and tags can retain removed credentials;
- a clean current checkout does not prove clean history;
- raw matching values must never be emitted into logs or artifacts;
- full-history checkout and object traversal must be explicit and reproducible;
- binary and oversized objects require honest completeness accounting rather than silent skipping.

## Considered approaches

### Approach A — run the current tree scanner against every commit checkout

This is conceptually simple but scales as commits multiplied by files, repeatedly scans identical blobs, and creates a large temporary working-tree attack surface. It also complicates path tracking and cleanup.

**Rejected.**

### Approach B — add an external scanner action

A mature external scanner can be useful, but adding a network-fetched binary or floating action expands supply-chain authority and makes local reproducibility harder. It would also duplicate Nexus's existing redaction and allowlist contract.

**Rejected for this bounded slice.** A future independent evaluation can compare this native control with an external scanner.

### Approach C — scan unique reachable Git blobs with the existing Nexus detector

Use Git plumbing to enumerate all objects reachable from every local branch and tag, resolve object type and size, and stream each eligible unique blob once. Reuse the existing secret patterns, placeholder suppression, fingerprinting and allowlist semantics through a new public `scan_secret_text` function.

**Selected.** It is deterministic, offline, incremental in conceptual authority, and does not mutate history or expose secret values.

## Architecture

### 1. Shared text detector

`scripts/security/scanner.py` gains:

```python
def scan_secret_text(relative_path: str, text: str) -> list[Finding]:
    ...
```

The function owns line enumeration, placeholder rejection, pattern matching and fingerprint generation. Existing `scan_secret_files` delegates to it, preserving current-tree behavior while preventing pattern drift between tree and history scans.

No secret value is returned. A `Finding` contains only rule, path, line and a one-way truncated SHA-256 fingerprint.

### 2. Reachable object enumeration

`scripts/security/scan_git_history.py` runs only read-only Git commands:

- `git rev-list --objects --all` to enumerate reachable object IDs and best-known paths;
- `git cat-file --batch-check` to resolve object type and size;
- `git cat-file --batch` to stream selected blob contents;
- `git rev-list --all --count` for commit count;
- `git for-each-ref` to create a digest-bound reference snapshot without publishing raw reference names.

Object IDs are validated as 40-character lowercase hexadecimal SHA-1 identifiers because this repository currently uses SHA-1 object format. Malformed Git output, unexpected object types in the batch stream, missing objects or command failure terminate the scan.

Only unique reachable blobs are scanned once. If one blob has multiple historical names, the lexicographically first bounded path is used for deterministic evidence.

### 3. Text and completeness policy

Every reachable blob is classified:

- size at or below `MAX_FILE_BYTES`: read once; NUL-bearing or non-UTF-8 content is counted as binary/unreadable and not pattern-scanned;
- size above `MAX_FILE_BYTES` with a known binary suffix: counted as binary oversized and considered intentionally non-text;
- size above `MAX_FILE_BYTES` without a known binary suffix: counted as unscanned oversized and makes the report incomplete/failing.

The scanner does not silently claim full coverage when an unknown or text-like oversized blob was not inspected.

Known binary suffixes are a narrow explicit list for common images, archives, media, fonts, compiled objects and databases. An extensionless or unknown oversized object is never presumed safe.

### 4. Findings and allowlist

For each decoded text blob, the shared detector runs using the historical path. Findings are deduplicated by `(path, rule, line, fingerprint)` so an unchanged secret appearing across many commits is reported once.

The existing `config/security/secret-scan-allowlist.json` contract is reused. An allowlist entry suppresses only the exact path/rule/fingerprint tuple and must remain non-expired. History-specific broad exemptions are not introduced.

The report retains at most 100 finding records while continuing to count all findings by rule. `truncated=true` is explicit when the stored list is capped.

### 5. Bounded evidence contract

The report schema is `nexus_security_git_history_scan_v1` and contains only:

- status: `pass` or `fail`;
- completeness boolean;
- current source SHA;
- reference-set SHA-256;
- commit/object/blob/scanned/binary/oversized counts;
- finding and suppressed counts;
- bounded counts by rule;
- findings with rule, historical path, line, fingerprint and blob SHA;
- truncation flag.

No matched value, source line, blob content, commit message, author identity, email, branch name, tag name, Provider payload, customer data or tool payload is emitted.

Report serialization is capped at 64 KiB and then passed through the existing artifact leak scanner before upload.

### 6. Workflow

`.github/workflows/git-history-secret-assurance.yml` runs on pull requests to `main`, pushes to `main`, and manual dispatch.

It uses immutable Action SHAs, `persist-credentials: false`, `fetch-depth: 0`, and read-only repository permissions. It:

1. compiles the scanner;
2. runs focused tests;
3. scans all reachable history;
4. captures the scanner exit status without losing the report;
5. validates the report with the existing artifact scanner;
6. uploads only the bounded JSON reports;
7. exits with the original scan status.

A finding or incomplete scan blocks the workflow. The workflow does not rotate, revoke, rewrite, delete or change visibility.

## Error handling

Expected failures are represented by a bounded reason code on stderr and a non-zero exit. Git command stderr and source content are not copied into the evidence report.

Unexpected Python failures remain visible to CI and do not produce a false pass. Missing full history, malformed object metadata, object read mismatch, report overflow, invalid allowlist or artifact-scan failure all block completion.

## Security and privacy

- Read-only Git commands only.
- No network dependency after checkout.
- No raw match values in memory beyond the current decoded blob and regex match lifetime.
- No raw values in logs or reports.
- Immutable workflow dependencies.
- Existing artifact leak scanner validates generated evidence.
- No credentials are used beyond GitHub's read-only checkout token, which is not persisted.

## Compatibility and rollback

The code change to `scanner.py` is a behavior-preserving extraction: `scan_secret_files` delegates to the same detector and retains its public result contract.

All other files are additive. Reverting the PR removes the history scan and workflow without changing runtime, database, Provider, outbound, deployment or repository visibility behavior.

## Acceptance boundary

This slice is ready for review when exact-head evidence proves:

- a secret committed and removed from HEAD is detected;
- placeholders are ignored consistently with the existing scanner;
- findings never contain the raw secret;
- duplicate historical blobs do not create duplicate findings;
- known binary and unknown oversized objects are distinguished;
- an unknown oversized object makes the scan incomplete/failing;
- allowlist semantics remain exact and expiry-bound;
- the real repository full-history scan produces a bounded artifact;
- all changed paths remain within the declared claim;
- no visibility, rotation or history rewrite occurs.

A failing real-history result is valid security evidence but blocks merge readiness. Remediation then requires a separately authorized #565 slice for secret triage, credential rotation/revocation and, where approved, history rewriting.