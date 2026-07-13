# Reachable Git History Secret Assurance — Design

## Status and authority

- Work Item: #565
- Parent audit: #545 / `NEX-AUD-001`
- Baseline: `main@e96dac837b4f02a297602259953d7c274b9c2063`
- Delivery class: non-destructive security evidence
- Historical PR #603: evidence only; closed without merge

## Problem

The current-tree secret scanner enumerates tracked files in one checkout. A credential can be removed from HEAD while remaining reachable in an earlier Blob through a branch or tag. Historical #603 attempted to close this gap, but its scanner tests failed, the real scan was skipped, and evidence upload failed. That failure sequence did not establish either a clean history or a confirmed active credential.

## Constraints

The implementation must:

- perform read-only Git operations;
- use the current Nexus credential rules, placeholder handling and fingerprints;
- retain the current-tree scanner's existing 200-finding boundary;
- count every logical history finding without a 200-finding blind spot;
- output only bounded redacted metadata;
- reject shallow or incomplete history;
- account for every reachable Blob;
- produce a minimal report even when tests or scanning fail;
- perform no credential, history, visibility, deployment or production mutation.

## Selected architecture

### Unique reachable blobs

Use Git plumbing rather than checking out every commit:

- `rev-list --objects --all` enumerates reachable object IDs and internal paths;
- `cat-file --batch-check` resolves type and size;
- `cat-file --batch` streams eligible Blobs;
- `for-each-ref` provides object IDs only for a reference-set digest;
- `rev-parse --show-object-format` supports SHA-1 and SHA-256 repositories.

Every unique eligible Blob is read once.

### Shared detection semantics

The history scanner imports the current `scanner.py` pattern table, placeholder predicate and fingerprint function. It performs an uncapped internal iteration over those rules. The public current-tree `scan_secret_files()` remains unchanged and capped at 200.

This avoids changing the current report contract while preventing history totals from being silently truncated.

### Logical deduplication

A logical finding is identified internally by:

```text
path + rule + line + fingerprint
```

Blob object ID is not part of the logical identity. Therefore an unchanged credential in a file that gains unrelated content is reported once.

The raw path is retained only inside scanner memory for fingerprint and exact allowlist matching. The emitted report replaces it with a SHA-256 path digest and bounded suffix.

### Completeness accounting

Each reachable Blob is classified exactly once as:

- scanned UTF-8 text;
- binary or unreadable;
- known oversized binary;
- unknown oversized and unscanned.

`accounted_blob_count` must equal `reachable_blob_count`. Any unknown oversized Blob makes `complete=false` and blocks the result.

The dedicated Workflow scans up to 8 MiB. This evidence-driven ceiling covered the observed 5.9 MiB non-binary Blob. Future unknown Blobs over 8 MiB remain blocking rather than being silently classified.

### Evidence

The report stores at most 100 findings while maintaining complete totals and per-rule counts. It includes path SHA-256, path suffix, line number, finding fingerprint and Blob object ID, but no raw path, content, commit metadata or reference name.

Rules are emitted as values in a bounded list rather than JSON keys, allowing the generic artifact scanner to validate a finding-bearing report without mistaking a rule label for secret material.

### Exact historical allowlist

The current-main scan identified only known synthetic test fixtures and historical `.venv` third-party example material. Suppression uses the existing allowlist contract:

```text
exact internal path + exact rule + exact fingerprint + reason + expiry
```

No wildcard, non-expiring or rule-only exception is accepted. Raw paths remain absent from evidence.

### Failure evidence

Tests, history scan and artifact scan have distinct statuses. If a normal history report is missing, the Workflow creates a minimal schema-valid failure report. It then creates an always-present numeric status report and uploads all bounded JSON files before enforcing the final gate.

This prevents evidence-upload failure from masking the original failure.

## Security analysis

- Actions are pinned to immutable SHAs.
- Checkout is full-history, read-only and does not persist credentials.
- Raw Git stderr is discarded from evidence.
- Scanner stdout contains only safe counts and status.
- Generated evidence is scanned before upload.
- Report size is limited to 64 KiB.
- A finding blocks merge but triggers no remediation action.
- Allowlist entries are exact and time-bounded.

## Rejected approaches

### Re-checkout every commit

Rejected because it repeatedly scans identical Blobs, increases runtime and creates a larger temporary working-tree surface.

### External scanner action

Rejected for this slice because it adds supply-chain authority and duplicates existing Nexus redaction/allowlist semantics.

### Silent skip of oversized content

Rejected because it permits a false clean result.

### Raw historical paths in uploaded evidence

Rejected because paths can contain personal, tracking or operational identifiers. Path digests preserve equality and triage correlation without disclosure.

## Acceptance

One exact final Head must prove:

- all focused and existing security tests pass;
- a removed historical secret is detected without raw disclosure;
- same logical finding across changed Blobs is deduplicated;
- more than 200 history findings are fully counted;
- shallow repositories and incomplete coverage fail closed;
- the real Nexus non-shallow history scan completes;
- every reachable Blob is accounted for;
- the bounded evidence artifact passes leak scanning;
- all repository checks and independent review pass.

A real finding or incomplete scan is valid blocking evidence. Credential triage, rotation/revocation and history rewriting remain separately authorized #565 work.
