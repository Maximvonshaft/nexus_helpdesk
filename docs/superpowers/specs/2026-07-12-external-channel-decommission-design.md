# ExternalChannel Decommission Inventory and Reintroduction Gate — Design

## Authority and scope

This design implements the first non-destructive slice of Work Item #572 (`NEX-AUD-014`). The user authorized autonomous selection and delivery of eligible Nexus OSR Work Items, while #572 supplies the stable outcome, safety boundary and acceptance criteria. The session announced the selected slice before implementation: establish an authoritative asset inventory and an architecture gate without deleting data, changing active WhatsApp/Provider routing, deploying, or mutating production.

The design follows the Superpowers workflow: inspect current context, compare approaches, choose a bounded design, write the specification, write a stepwise implementation plan, use test-first implementation, request review, and verify the exact head before any completion claim.

## Problem statement

ExternalChannel is retired as a transport, but the repository still contains materially different reference classes:

- compatibility APIs that return a bounded disabled status;
- runtime and service code that can still write legacy tables or attachments;
- models, schemas and historical migrations needed to read existing data;
- settings and deployment examples that continue to parse retired configuration;
- frontend and operator compatibility surfaces;
- tests, reports and documentation that are historical evidence;
- bootstrap monkey patches that keep old persistence behavior alive.

A text deletion campaign would be unsafe. The same marker can identify an active write path, a compatibility response, a historical migration or an audit report. Before callers can be migrated and stores removed, Nexus needs one machine-verifiable disposition for every current asset and a permanent control that prevents unreviewed reintroduction.

## Considered approaches

### Approach A — delete all references immediately

This maximizes apparent cleanup but violates #572. Historical tables may still contain readable data, operator/API compatibility may still have callers, and destructive schema removal requires #532 rehearsal. It would also create a broad PR with weak rollback and review boundaries.

**Rejected.**

### Approach B — documentation-only inventory

A Markdown table would be readable, but it would drift. New references could enter without updating the document, old helpers could regain production callers, and CI could not prove coverage.

**Rejected as insufficient.**

### Approach C — strict machine-readable inventory plus repository gate

Create a versioned JSON inventory, a standard-library checker, focused tests and a permanent GitHub Actions gate. Every discovered reference must match exactly one disposition rule. Production-capable roots require exact paths; globs are allowed only in explicitly non-runtime historical roots. Exact paths with identical metadata may be grouped for maintainability, but the checker expands them into individual exact rules before evaluation. Write-capable legacy assets must be declared and carry a stop-new-writes requirement.

**Selected.** It produces independent value now, enables later migration/removal, and does not conflict with active Provider, release-profile, TicketEvent or resilience PRs.

## Architecture

### 1. Versioned inventory

`config/governance/external-channel-assets.v1.json` is the source of truth for repository asset disposition.

Top-level contract:

- `schema`: exactly `nexus.external-channel-retirement.inventory.v1`;
- `inventory_version`: immutable semantic identifier for this inventory revision;
- `audited_main_sha`: exact main SHA used to establish the baseline;
- `discovery_tokens`: exactly the case-sensitive markers the checker scans;
- `production_roots`: prefixes where wildcard classification is forbidden;
- `allowed_historical_glob_roots`: prefixes where controlled globs are permitted;
- `rules`: exact selectors or historical glob selectors.

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

Grouped `paths` are a storage convenience only. They expand to independent `InventoryRule` objects, participate in duplicate/overlap detection individually, and count as separate exact rules in evidence.

Allowed dispositions:

- `active_compatibility` — intentionally callable/readable during migration;
- `historical_evidence` — retained as non-runtime evidence;
- `data_migration_dependency` — required for historical schema/data interpretation;
- `safe_to_remove` — no intended long-term authority, but prerequisites are not yet proven;
- `retirement_control` — inventory, checker, tests and workflow controls.

No disposition authorizes deletion, deployment or production mutation.

### 2. Discovery and validation checker

`scripts/ci/check_external_channel_retirement.py` uses only the Python standard library.

It will:

1. load JSON with duplicate-key rejection;
2. reject unknown or missing fields;
3. validate bounded identifiers, owners, rationale and prerequisites;
4. expand grouped exact paths and reject duplicates;
5. reject overlapping matches;
6. reject globs that could classify production roots;
7. require write surfaces to use exact selectors and set `stop_new_writes_required=true` with `caller_migration` as a prerequisite;
8. enumerate tracked files using `git ls-files -z`;
9. skip binary files identified by NUL bytes;
10. discover tracked text files containing a configured marker;
11. require every discovered file to match exactly one expanded rule;
12. require every exact path to exist and contain a discovery marker;
13. require every historical glob to match at least one current marker-bearing file;
14. emit a bounded summary containing counts, schema/version and a digest, never source contents or discovered payloads.

Failure output is a bounded reason code plus safe path metadata. It does not print source contents, customer data, credentials or raw Provider/tool payloads.

### 3. Test boundary

`scripts/ci/tests/test_check_external_channel_retirement.py` tests the real checker. Tests cover:

- a valid exact production asset and historical glob;
- grouped exact paths expanding into individual production assets;
- uncovered references;
- overlapping rules;
- forbidden production wildcards;
- malformed or unknown schema fields;
- invalid write-surface declarations;
- stale exact paths;
- exact paths that no longer contain a marker;
- duplicate JSON keys and duplicate rules;
- deterministic bounded summary generation.

The initial RED commit contained tests before the checker module. The grouped-selector behavior was also introduced test-first before the parser was expanded.

### 4. Permanent CI gate

`.github/workflows/external-channel-retirement-gate.yml` runs on relevant pull requests, pushes to main and manual dispatch. It executes:

- Python syntax compilation;
- focused checker tests;
- the actual tracked-repository inventory scan.

The workflow installs no application dependencies and performs no database, Provider, outbound, deployment or production mutation. Repository permissions are read-only.

### 5. Human decommission record

`docs/engineering/external-channel-decommission.md` explains classifications, known write-capable surfaces, later migration phases, evidence required before deletion and rollback.

Phases:

1. inventory and reintroduction gate — this slice;
2. prove caller, traffic and historical data-read state;
3. stop new legacy writes and migrate callers;
4. observe compatibility usage for a defined window;
5. remove safe code/configuration surfaces;
6. rehearse data/schema removal under #532;
7. execute a separately authorized destructive migration only if still justified.

## Production-path policy

Production roots include application code, deploy configuration, executable scripts, frontend code and GitHub workflows. A wildcard cannot classify references in these roots because it would allow a new runtime dependency to enter unnoticed. Every production-root reference therefore receives an explicit exact-path decision, either as `path` or as a member of a `paths` group.

Historical globs are permitted only under bounded roots such as `docs/`, `backend/tests/` and `backend/alembic/versions/`. A new matching production file fails the gate until an explicit rule and review are added.

## Error handling and fail-closed behavior

The checker exits non-zero for malformed JSON, unsupported schema, invalid fields/rules, missing tracked files, markerless exact paths, uncovered references, overlapping rules, forbidden globs, stale historical globs, invalid write controls or repository enumeration/read failures.

It never falls back to an empty inventory and does not swallow expected control-path errors. Unexpected Python failures remain visible as a traceback in the CI log rather than being mislabeled as a successful bounded check.

## Security and privacy

- No source-file contents are emitted.
- No live credential or endpoint value is stored in the inventory.
- No customer, tracking, contact, address, Provider payload or tool payload is emitted as evidence.
- Summary output is low-cardinality and digest-bound.
- The workflow has read-only repository permissions.
- The slice has no database, outbound, Provider, deployment or external-resource side effects.

## Compatibility and rollback

All files are additive. Reverting the PR removes the inventory and gate without changing runtime behavior or historical data. Rollback does not reactivate ExternalChannel; the existing disabled/compatibility behavior remains exactly as it was on the starting main.

## Acceptance boundary for this slice

This slice is accepted when:

- current discovered assets have an owner and disposition;
- production references require exact-path classification after group expansion;
- write-capable legacy surfaces are explicit;
- new unclassified references fail CI;
- focused tests and actual repository scan pass on the exact head;
- no current WhatsApp/Provider behavior is modified;
- no migration or destructive cleanup is introduced.

This is meaningful progress toward #572 but does not close it. Stop-new-writes migration, caller removal, observation evidence and any #532-backed destructive action remain separate reviewable slices.

## Spec self-review

- Placeholder scan: no TBD/TODO or deferred implementation ambiguity exists.
- Consistency: manifest, checker, tests, workflow and documentation share one schema and non-destructive boundary.
- Scope: one independently testable inventory/control slice; runtime migration and schema deletion are excluded.
- Ambiguity: production references are exact after expansion; only historical roots may use globs; write surfaces are exact and flagged.