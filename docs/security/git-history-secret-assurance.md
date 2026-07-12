# Git History Secret Assurance

## Purpose

The existing repository secret scanner validates the current tracked tree. It cannot prove that an earlier reachable commit did not contain a credential that was later deleted.

The Git history assurance control closes that evidence gap by scanning each unique regular Git blob reachable from local branches and tags. It is a read-only detection control. It does not change repository visibility, rotate credentials, revoke tokens, rewrite Git history, delete refs, deploy code or mutate production data.

## Authority

- Work Item: #565 (`NEX-AUD-001`)
- Scanner: `scripts/security/scan_git_history.py`
- Shared detector: `scripts/security/scanner.py`
- Workflow: `.github/workflows/git-history-secret-assurance.yml`
- Report schema: `nexus_security_git_history_scan_v1`
- Allowlist: `config/security/secret-scan-allowlist.json`

The workflow runs on every pull request to `main`, every push to `main`, and manual dispatch. Repository permissions are read-only. Checkout uses full history, disables credential persistence, and all external Actions are pinned to immutable commit SHAs.

## What is scanned

The scanner uses Git plumbing rather than repeatedly checking out commits:

1. `git rev-list --objects --all` enumerates every object reachable from local branches and tags.
2. `git cat-file --batch-check` resolves object type and size.
3. Each unique eligible blob is streamed once through `git cat-file --batch`.
4. UTF-8 text is scanned with the same credential patterns, placeholder rejection and fingerprint logic used by the current-tree scanner.

The report includes a digest of the reference snapshot, not branch or tag names. It does not include commit messages, authors, email addresses, source lines or matched values.

## Completeness policy

A scan is complete only when every reachable blob is accounted for:

- blobs at or below the size limit are read once;
- NUL-bearing or non-UTF-8 blobs are counted as binary/unreadable;
- oversized blobs with an explicit known-binary suffix are counted as oversized binary;
- oversized blobs without a known-binary suffix are counted as unscanned and make the result incomplete.

An incomplete scan is a failure. The control never silently converts an unknown oversized object into a clean result.

## Result interpretation

### `status=pass`, `complete=true`

The scanner found no unsuppressed credential-shaped material in the reachable blobs it was required to inspect.

This result does **not** prove that:

- the repository is safe to make public;
- no credential was exposed through another system;
- current credentials have been rotated;
- generated artifacts or container images are clean;
- repository visibility or access controls are correct.

Those remain separate #565 controls.

### `status=fail`, `complete=true`, `finding_count>0`

At least one reachable historical blob contains credential-shaped material. Evidence is intentionally redacted and contains only:

- rule;
- deterministic historical path;
- line number;
- one-way truncated fingerprint;
- blob object ID.

Do not copy a suspected value into an Issue, PR, chat, ticket or operational document. Triage should use the path, fingerprint and blob ID in an access-controlled environment.

### `status=fail`, `complete=false`

The scan could not make a complete assurance claim. Typical causes include an unknown oversized blob, malformed Git object metadata, object read failure or invalid allowlist.

Treat this as a blocking assurance failure, not as “no findings.”

## Allowlist policy

The history scanner reuses `nexus_secret_scan_allowlist_v1`. An entry suppresses only the exact tuple:

- repository path;
- rule;
- fingerprint.

Entries must have a concrete reason and an unexpired date. Broad path, rule-only or history-wide exemptions are not supported.

An allowlist is appropriate only for a verified synthetic fixture or an otherwise proven non-secret value. A real credential must be rotated or revoked; allowlisting it is not remediation.

## Bounded evidence

The scanner stores at most 100 finding records while continuing to count all unique findings. `findings_truncated=true` means the stored record list was capped; `finding_count` and `by_rule` remain authoritative totals.

Generated evidence is capped at 64 KiB and scanned again by `scripts/security/scan_artifacts.py` before upload. The uploaded artifact contains only:

- the bounded history report;
- bounded scanner stdout;
- the artifact-validation report;
- numeric exit-status evidence.

Artifact retention is 14 days.

## Remediation authority

A history finding does not authorize automatic cleanup. Required decisions are owner-controlled:

1. verify whether the finding is a real credential or a synthetic false positive;
2. identify the issuing system and blast radius;
3. rotate or revoke the credential before relying on repository cleanup;
4. determine whether downstream caches, forks, mirrors, release artifacts or logs also contain the value;
5. decide whether history rewriting is necessary and acceptable;
6. coordinate force-push, branch protection, tag replacement and collaborator re-cloning if rewriting is approved;
7. re-run current-tree, full-history and artifact assurance after remediation.

Repository visibility changes, credential operations and history rewriting require explicit authorization under #565. This workflow performs none of them.

## Failure handling

The workflow preserves the history scanner exit code, validates every generated JSON artifact, uploads bounded evidence even on failure, and then fails closed.

A real finding or incomplete scan is valid security evidence, but the implementing PR must remain Draft until an authorized remediation path is complete or the finding is proven synthetic through the strict allowlist process.

## Branch protection

The workflow creates the `git-history-secret-assurance` check. It does not itself modify GitHub branch-protection settings. Repository administrators should add the check to the required-check set where that policy is managed outside the repository.

## Rollback

This control is additive except for the behavior-preserving shared-detector extraction in `scripts/security/scanner.py`.

Rollback is a normal Git revert of the scanner, tests, workflow and documentation. It requires no database downgrade, production cleanup, Provider action, credential operation, ref deletion or history rewrite.
