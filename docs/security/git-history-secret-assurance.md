# Reachable Git History Secret Assurance

## Purpose

The tracked-tree scanner proves only that the current checkout has no unsuppressed credential-shaped finding. It cannot prove that a credential committed previously and removed later is absent from reachable branches or tags.

The reachable-history assurance control scans every unique regular Git blob reachable from the full local reference set. It is read-only. It does not rotate credentials, rewrite history, delete refs, change repository visibility, deploy code, or mutate production data.

## Authority

- Work Item: #565 (`NEX-AUD-001`)
- Scanner: `scripts/security/scan_git_history.py`
- Current-tree detector source: `scripts/security/scanner.py`
- Workflow: `.github/workflows/git-history-secret-assurance.yml`
- Report schema: `nexus_security_git_history_scan_v1`
- Status schema: `nexus_security_git_history_assurance_status_v1`
- Allowlist: `config/security/secret-scan-allowlist.json`

## Coverage model

The scanner:

1. rejects shallow repositories;
2. detects the repository object format;
3. enumerates every object reachable from local branch, remote-tracking and tag references;
4. resolves object type and declared size through Git batch metadata;
5. enumerates every `(Blob, path)` pair from all unique reachable commit root Trees;
6. reads each eligible unique Blob once, but evaluates every historical path alias independently;
7. evaluates every match produced by every credential rule, including repeated same-rule values on one line;
8. accounts for every Blob as scanned text, binary/unreadable, known oversized binary, or unknown oversized;
9. fails closed if any unknown oversized Blob remains unscanned;
10. uses the same credential patterns, placeholder policy and fingerprint algorithm as the current-tree scanner.

A path-specific allowlist therefore applies only to that exact path/rule/fingerprint tuple. Copying or renaming identical Blob content to another path creates a separate allowlist decision; one fixture path cannot suppress a non-fixture alias.

The current-tree scanner retains its existing 200-record contract. The history scanner counts all logical findings while storing no more than 100 redacted records.

The dedicated Workflow scans Blobs up to 8 MiB. This ceiling covered every non-binary reachable Blob at the accepted current-main run. Any future unknown Blob above the ceiling makes the result incomplete and blocks the gate.

## Evidence boundary

Evidence may contain only:

- bounded repository source and reference digests;
- object, Blob, Blob-path and accounting counts;
- bounded counts by rule;
- SHA-256 of the historical path and its bounded suffix;
- line number;
- truncated one-way finding fingerprint;
- Git Blob object ID.

Evidence must not contain:

- raw historical paths;
- matched values or source lines;
- commit messages;
- author names or email addresses;
- branch or tag names;
- credential values;
- customer, Provider or tool payloads;
- raw Git stderr.

Generated reports are limited to 64 KiB and scanned again before upload. Artifact retention is 14 days.

## Result interpretation

### Pass

`status=pass` and `complete=true` means:

- every reachable Blob was accounted for;
- every reachable historical path alias was independently evaluated;
- no unsuppressed credential-shaped finding was detected;
- generated evidence passed artifact scanning.

It does not authorize making the repository public or prove that credentials were never exposed through another system.

### Finding

`status=fail`, `complete=true`, and `finding_count>0` means at least one logical finding remains reachable. Do not copy the suspected value or raw path into an Issue, PR, chat or document. Use the bounded path digest, rule, finding fingerprint and Blob object ID only in an access-controlled triage environment.

Credential rotation or revocation must occur before any history-cleanup decision. Such action requires separate owner authorization under #565.

### Incomplete

`status=fail` and `complete=false` means the scanner could not make a complete assurance claim. Causes include a shallow repository, malformed Git metadata, incomplete path-alias enumeration, object read mismatch, invalid allowlist, unknown oversized Blob or missing evidence.

Incomplete is blocking evidence, not a clean result.

## Allowlist policy

An allowlist entry is valid only for the exact internal path, rule and fingerprint tuple, with a concrete reason and an unexpired date. The raw path is used only inside the scanner for exact matching and is not emitted in evidence.

Allowlisting is intended for proven synthetic fixtures or third-party example material. A real credential must not be allowlisted as remediation. Every accepted historical tuple remains time-bounded and must be re-reviewed before expiry.

## Workflow behavior

The Workflow uses immutable Action SHAs, read-only repository permission, full-history checkout and non-persisted checkout credentials.

Tests, the history scan and the artifact scan have separate exit evidence. If tests or scanning fail before a normal report is produced, the Workflow creates a minimal bounded failure report before uploading evidence. Upload failure can no longer hide the original scanner failure.

## Remediation authority

A finding does not authorize automatic cleanup. Separately authorized work must decide:

1. whether the finding is real or synthetic;
2. issuing system and blast radius;
3. rotation or revocation;
4. downstream forks, mirrors, caches, artifacts and logs;
5. whether rewriting history is necessary;
6. force-push, branch protection, tag replacement and collaborator recovery;
7. post-remediation tree, history and artifact revalidation.

## Rollback

Revert the scanner, tests, Workflow, allowlist additions and documentation. No database downgrade, deployment cleanup, Provider action, credential action or history rewrite is required.
