# ExternalChannel Decommission Inventory and Reintroduction Gate — Design

## Authority and scope

This design implements the first non-destructive slice of Work Item #572 (`NEX-AUD-014`). The user authorized autonomous selection and delivery of eligible Nexus OSR Work Items, while #572 supplies the outcome, safety boundary and acceptance criteria.

The slice establishes an authoritative repository inventory and a permanent architecture gate. It does not delete data, change active WhatsApp/Provider routing, deploy, mutate production or authorize destructive schema work.

The delivery follows the Superpowers workflow: inspect current context, compare approaches, select a bounded design, write the specification and plan, implement test-first, review the result, and require fresh exact-head verification before any completion claim.

## Problem statement

ExternalChannel is retired as a transport, but the repository still contains materially different reference classes:

- compatibility APIs that return a bounded disabled status;
- runtime and service code that can still write legacy tables or attachments;
- models, schemas and historical migrations needed to read existing data;
- settings and deployment examples that continue to parse retired configuration;
- frontend and operator compatibility surfaces;
- tests, reports and documentation that are historical evidence;
- bootstrap monkey patches that keep old persistence behavior alive.

The same marker can identify an active write path, a compatibility response, a historical migration or an audit report. A text deletion campaign would therefore be unsafe. Nexus first needs one machine-verifiable disposition for every current asset and a control that prevents unreviewed reintroduction.

## Considered approaches

### Approach A — delete all references immediately

This maximizes apparent cleanup but violates #572. Historical tables may still contain readable data, operator/API compatibility may still have callers, and destructive schema removal requires #532 rehearsal. It would also create a broad PR with weak rollback and review boundaries.

**Rejected.**

### Approach B — documentation-only inventory

A Markdown table would be readable but would drift. New references could enter without updating the document, old helpers could regain production callers, and CI could not prove coverage.

**Rejected as insufficient.**

### Approach C — strict machine-readable inventory plus full-tree repository gate

Create a versioned JSON inventory, a standard-library checker, focused tests and a permanent GitHub Actions gate. Every discovered reference must match exactly one disposition rule. Production-capable roots require exact paths; globs are allowed only in explicitly non-runtime historical roots. Exact paths with identical metadata may be grouped for maintainability, but the checker expands them into individual exact rules before evaluation. Write-capable legacy assets must carry a stop-new-writes requirement.

The workflow runs for every change targeting `main`, not only a preselected directory list. This is necessary because an unanticipated directory is itself a possible reintroduction vector.

**Selected.** It produces independent value now, enables later migration/removal, and does not conflict with active Provider, release-profile, TicketEvent or resilience work.

## Architecture

### 1. Versioned inventory

`config/governance/external-channel-assets.v1.json` is the source of truth for repository asset disposition.

Top-level contract:

- `schema`: exactly `nexus.external-channel-retirement.inventory.v1`;
- `inventory_version`: immutable semantic identifier for the inventory revision;
- `audited_main_sha`: exact main SHA used to establish the baseline;
- `discovery_tokens`: exact ordered case-sensitive marker set;
- `production_roots`: prefixes where wildcard classification is forbidden;
- `allowed_historical_glob_roots`: prefixes where controlled globs are permitted;
- `rules`: exact selectors or historical glob selectors.

The marker set is:

```text
ExternalChannel
external_channel
EXTERNAL_CHANNEL
externalChannel
external-channel
```

Each rule contains exactly one selector:

- `path`: one exact tracked path;
- `paths`: a non-empty group of exact tracked paths sharing identical metadata; or
- `glob`: a historical pattern rooted under an approved non-runtime prefix.

Every rule also contains:

- `asset_type`;
- `disposition`;
- `owner`;
- `rationale`;
- `write_surface`;
- `stop_new_writes_required`;
- `prerequisites`.

Grouped `paths` are a storage convenience only. They expand to independent `InventoryRule` objects, participate in duplicate and overlap detection individually, and count as separate exact rules in evidence.

Allowed dispositions:

- `active_compatibility` — intentionally callable or readable during migration;
- `historical_evidence` — retained as non-runtime evidence;
- `data_migration_dependency` — required for historical schema or data interpretation;
- `safe_to_remove` — no intended long-term authority, but prerequisites are not yet proven;
- `retirement_control` — inventory, checker, tests and workflow controls.

No disposition authorizes deletion, deployment or production mutation.

### 2. Discovery boundary

`scripts/ci/check_external_channel_retirement.py` uses only the Python standard library.

Tracked entries are enumerated with `git ls-files -z --stage`. The checker:

- scans regular blobs with mode `100644` or `100755`;
- excludes symlinks (`120000`) and gitlinks/submodules (`160000`) explicitly;
- rejects malformed index records and unknown modes;
- discovers an asset when either the tracked repository path or its non-binary contents contain any configured marker;
- still discovers a binary file when its path contains a marker;
- never opens excluded gitlinks or symlinks as local files.

Path discovery is required because an empty, binary or unusual-encoding file with a retired marker in its name is still an asset. Content discovery covers references whose filenames are generic.

### 3. Validation boundary

The checker:

1. loads JSON with duplicate-key rejection;
2. rejects unknown or missing fields;
3. validates bounded identifiers, owners, rationale and prerequisites;
4. expands grouped exact paths and rejects duplicates;
5. rejects overlapping matches;
6. rejects globs that could classify production roots;
7. requires write surfaces to use exact selectors and set `stop_new_writes_required=true` with `caller_migration` as a prerequisite;
8. requires every discovered file to match exactly one expanded rule;
9. requires every exact path to remain tracked and discovered;
10. requires every historical glob to match at least one current discovered file;
11. emits a bounded summary containing counts, schema/version and a digest, never source contents or discovered payloads.

Failure output is a bounded reason code plus safe repository path metadata. It does not print source contents, customer data, credentials or raw Provider/tool payloads.

### 4. Test boundary

`scripts/ci/tests/test_check_external_channel_retirement.py` tests the real checker. Coverage includes:

- valid exact production assets and historical globs;
- grouped exact paths expanding into individual rules;
- Git regular-file, symlink and gitlink modes;
- malformed Git index records;
- lower-camel and hyphen marker variants;
- marker discovery in paths and contents;
- uncovered and ambiguous references;
- forbidden production wildcards;
- malformed or unknown schema fields;
- invalid write-surface declarations;
- stale exact paths and markerless exact rules;
- duplicate JSON keys and duplicate rules;
- deterministic bounded summary generation.

The initial RED commit contained tests before the checker module. Later grouped-selector, Git-index, marker-variant and path-discovery changes were also introduced test-first before their production fixes.

### 5. Permanent CI gate

`.github/workflows/external-channel-retirement-gate.yml` runs on:

- every pull request targeting `main`;
- every push to `main`;
- manual dispatch.

It executes:

- Python syntax compilation;
- focused checker tests;
- the actual tracked-repository inventory scan;
- upload of a bounded result artifact for both success and failure diagnosis.

The workflow installs no application dependencies and performs no database, Provider, outbound, deployment or production mutation. Repository permissions are read-only.

The workflow is intentionally not path-filtered. A path allowlist would allow an unanticipated directory to bypass a full-tree control.

### 6. Human decommission record

`docs/engineering/external-channel-decommission.md` explains classifications, known write-capable surfaces, later migration phases, evidence required before deletion and rollback.

Phases:

1. inventory and reintroduction gate — this slice;
2. prove caller, traffic and historical data-read state;
3. stop new legacy writes and migrate callers;
4. observe compatibility usage for a defined window;
5. remove safe code and configuration surfaces;
6. rehearse data or schema removal under #532;
7. execute a separately authorized destructive migration only if still justified.

## Production-path policy

Production roots include application code, deploy configuration, executable scripts, frontend code and GitHub workflows. A wildcard cannot classify references in these roots because that would allow a new runtime dependency to enter unnoticed. Every production-root reference receives an explicit exact-path decision, either as `path` or as a member of a `paths` group.

Historical globs are permitted only under bounded roots such as `docs/`, `backend/tests/`, `webapp/e2e/` and `backend/alembic/versions/`. A new matching production file fails the gate until an explicit rule and review are added.

## Error handling and fail-closed behavior

The checker exits non-zero for malformed JSON, unsupported schema, invalid fields or rules, malformed Git index data, unknown Git modes, missing tracked files, markerless exact paths, uncovered references, overlapping rules, forbidden globs, stale historical globs, invalid write controls or repository enumeration/read failures.

It never falls back to an empty inventory and does not swallow expected control-path errors. Unexpected Python failures remain visible in the CI log rather than being mislabeled as a successful bounded check.

## Security and privacy

- No source-file contents are emitted.
- No live credential or endpoint value is stored in the inventory.
- No customer, tracking, contact, address, Provider payload or tool payload is emitted as evidence.
- Summary output is low-cardinality and digest-bound.
- The workflow has read-only repository permissions.
- The slice has no database, outbound, Provider, deployment or external-resource side effects.

## Compatibility and rollback

All files are additive. Reverting the PR removes the inventory and gate without changing runtime behavior or historical data. Rollback does not reactivate ExternalChannel; the existing disabled compatibility behavior remains exactly as it was on the starting main.

## Acceptance boundary for this slice

The slice is ready for review only when fresh exact-head evidence proves:

- current discovered assets have an owner and disposition;
- all five marker variants are detected in paths and contents;
- production references require exact-path classification after group expansion;
- write-capable legacy surfaces are explicit;
- new unclassified references fail CI regardless of directory;
- focused tests and the actual repository scan pass on the exact head;
- no current WhatsApp/Provider behavior is modified;
- no migration or destructive cleanup is introduced.

This is meaningful progress toward #572 but does not close it. Stop-new-writes migration, caller removal, observation evidence and any #532-backed destructive action remain separate reviewable slices.

## Spec self-review

- Placeholder scan: no TBD/TODO or deferred implementation ambiguity exists.
- Consistency: manifest, checker, tests, workflow and documentation share one schema and non-destructive boundary.
- Scope: one independently testable inventory/control slice; runtime migration and schema deletion are excluded.
- Ambiguity: production references are exact after expansion; only approved historical roots may use globs; write surfaces are exact and flagged; the gate runs on every main change.
