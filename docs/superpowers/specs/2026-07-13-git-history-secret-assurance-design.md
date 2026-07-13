# Reachable Git History Secret Assurance — Design

## Status and authority

- Work Item: #565
- Parent audit: #545 / `NEX-AUD-001`
- Baseline: current `main`
- Delivery class: non-destructive security evidence
- Historical PR #603: evidence only; closed without merge

## Problem

The current-tree scanner cannot prove that credentials removed from HEAD are absent from reachable Git history. Historical PR #603 failed its tests, skipped the real scan, and then failed evidence upload, so it established neither a clean result nor a confirmed finding.

The control must also resist these bypasses:

- identical Blob content at multiple historical paths;
- multiple same-rule values on one line, including the same value repeated;
- tags or refs that point directly to Trees;
- paths distinguished only by leading/trailing whitespace;
- oversized text hidden behind binary-looking suffixes;
- credential-shaped path suffixes leaking into evidence;
- tainted evidence being uploaded before the artifact scan blocks it.

## Constraints

The implementation must:

- use read-only Git operations;
- retain the current-tree scanner's 200-finding contract;
- reuse current credential patterns and placeholder semantics;
- count every logical historical occurrence;
- preserve exact decoded Git paths for fingerprint and allowlist matching;
- evaluate every reachable `(Blob, path)` alias;
- treat every Blob above the configured ceiling as incomplete;
- emit only bounded redacted metadata;
- produce sanitized failure evidence even when tests or scanning fail;
- perform no credential, history, visibility, deployment or production mutation.

## Architecture

### Reachable objects and path aliases

The scanner uses:

- `rev-list --objects --all` for the complete reachable object set;
- `cat-file --batch-check` for object type and size;
- `git log --all --format=%T` for commit root Trees;
- `for-each-ref` plus validated `<object-id>^{tree}` peeling for branch, remote and tag roots, including Tree-targeting tags;
- `ls-tree -r -z --full-tree` for every `(Blob, path)` pair;
- `cat-file --batch` to read each eligible unique Blob once;
- `rev-parse --show-object-format` for SHA-1/SHA-256 support.

Root Trees and `(Blob, path)` pairs are independently deduplicated. Blob bytes are read once; decoded content is evaluated under every path alias.

### Exact path semantics

NUL-delimited `ls-tree` paths are decoded without trimming. Leading/trailing whitespace remains part of fingerprint and allowlist identity. Only absolute paths, traversal, backslashes, NUL and line breaks are rejected.

The shared allowlist parser also preserves the exact path string. It does not normalize whitespace.

### Credential occurrences

Each rule uses `finditer()` rather than first-match search. The first occurrence of a path/rule/line/value keeps the existing 16-character base fingerprint for compatibility. Repeated identical values on the same line receive deterministic occurrence-derived fingerprints. Therefore one allowlist tuple cannot suppress multiple identical occurrences.

Logical identity remains:

```text
path + rule + line + occurrence fingerprint
```

Blob ID is evidence metadata, not logical identity, so the same occurrence in a later Blob revision is deduplicated while aliases and distinct occurrences remain independent.

### Completeness accounting

Each reachable Blob is exactly one of:

- scanned UTF-8 text;
- binary or unreadable after actual byte inspection;
- oversized and unscanned.

`accounted_blob_count` must equal `reachable_blob_count`. Every Blob above the ceiling makes `complete=false`, regardless of suffix. The Workflow currently scans up to 8 MiB; a future larger Blob blocks until the ceiling is explicitly reviewed and its bytes are scanned.

### Evidence boundary

The report may contain:

- source and reference digests;
- object, Blob, Blob-path and accounting counts;
- bounded rule counts;
- path SHA-256;
- a suffix only when it matches `.[a-z0-9]{1,16}`; otherwise empty;
- line number, occurrence fingerprint and Blob object ID.

It must not contain raw paths, matched values, source lines, commit/ref names, author data, Git stderr or unsafe suffix text.

### Exact allowlist

Suppression requires:

```text
exact untrimmed internal path + exact rule + exact occurrence fingerprint + reason + expiry
```

Wildcards, rule-only entries, non-expiring entries and credential remediation by allowlist are rejected.

### Failure and upload sequencing

Tests, history scan and artifact scan have separate status evidence. Missing reports are replaced with minimal schema-valid failure reports.

The complete history report is uploaded only when the artifact scan exit code is zero. If evidence scanning fails, only the sanitized numeric exit-status JSON is uploaded; the potentially tainted history report is never published as an artifact. The final gate still fails.

## Security analysis

- Actions use immutable SHAs and read-only permissions.
- Checkout is full-history and does not persist credentials.
- Git revision expressions contain validated object IDs and are passed without a shell.
- Git stderr is discarded from evidence.
- Reports are capped at 64 KiB and scanned before upload.
- Blob-path enumeration is bounded and fails closed.
- No oversized Blob is trusted by filename.
- Findings trigger no automatic credential or history mutation.

## Rejected approaches

- **Checkout every commit:** duplicates Blob reads and expands temporary working-tree surface.
- **One representative path per Blob:** widens exact allowlists across aliases.
- **Commit Trees only:** misses direct Tree tags.
- **Trim paths:** collapses distinct valid Git names.
- **First match only:** misses later same-rule values.
- **Deduplicate identical same-line values:** lets one tuple suppress multiple occurrences.
- **Trust binary suffixes:** permits unscanned text Blobs.
- **Emit raw suffixes:** can disclose credential-like path material.
- **Upload before evidence validation:** can publish tainted reports.

## Acceptance

One exact final Head must prove:

- all existing and focused security tests pass;
- removed historical credentials are detectable without raw disclosure;
- changed-Blob deduplication remains correct;
- aliases, Tree tags and whitespace-distinct paths are independent;
- all same-rule matches and repeated identical occurrences are counted;
- oversized binary-looking Blobs remain incomplete;
- unsafe suffixes are not emitted;
- the real non-shallow Nexus history scan completes;
- every reachable Blob is accounted for and every historical alias evaluated;
- generated evidence passes scanning before full upload;
- repository checks, Python/JS CodeQL and independent review pass.

A finding or incomplete scan is blocking evidence. Credential rotation, revocation or history rewriting remains separately authorized #565 work.
