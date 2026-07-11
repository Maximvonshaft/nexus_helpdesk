# ExternalChannel Decommission Inventory and Reintroduction Gate — Design

## Authority and scope

This design implements the first non-destructive slice of Work Item #572 (`NEX-AUD-014`). The user authorized autonomous selection and delivery of eligible Nexus OSR Work Items, while #572 supplies the stable outcome, safety boundary and acceptance criteria. The session announced the selected slice before implementation: establish an authoritative asset inventory and an architecture gate without deleting data, changing active WhatsApp/Provider routing, deploying, or mutating production.

The design follows the Superpowers workflow: inspect current context, compare approaches, choose a bounded design, write the specification, write a stepwise implementation plan, use test-first implementation, request review, and verify the exact head before any completion claim.

## Problem statement

ExternalChannel is retired as a transport, but the repository still contains several materially different classes of references:

- compatibility APIs that return a bounded disabled status;
- runtime and service code that can still write legacy tables or attachments;
- models, schemas and historical migrations needed to read existing data;
- settings and deployment examples that continue to parse retired configuration;
- frontend and operator compatibility surfaces;
- tests, reports and documentation that are historical evidence;
- bootstrap monkey patches that keep old persistence behavior alive.

A text deletion campaign would be unsafe. The same token can identify an active write path, a compatibility response, a historical migration or an audit report. Before callers can be migrated and stores removed, Nexus needs one machine-verifiable disposition for every current asset and a permanent control that prevents unreviewed reintroduction.

## Considered approaches

### Approach A — delete all ExternalChannel references immediately

This maximizes apparent cleanup but violates #572. Historical tables may still contain readable data, active operator/API compatibility may still have callers, and destructive schema removal requires #532 rehearsal. It also creates a high-risk, broad PR whose rollback and review boundaries are weak.

**Rejected.**

### Approach B — documentation-only inventory

A Markdown table would be easy to review and useful for humans, but it would drift immediately. New references could be introduced without updating the document, broad legacy helpers could regain production callers, and CI could not prove coverage.

**Rejected as insufficient.**

### Approach C — strict machine-readable inventory plus repository gate

Create a versioned JSON inventory, a standard-library checker, focused tests and a permanent GitHub Actions gate. Every discovered reference must match exactly one disposition rule. Production-capable roots require exact-path entries; globs are allowed only in explicitly non-runtime historical roots. Write-capable legacy assets must be declared and carry a stop-new-writes requirement. The first slice remains additive and reversible.

**Selected.** It produces independent value now, enables later caller migration and deletion, and does not compete with active Provider, release-profile, TicketEvent or resilience PRs.

## Architecture

### 1. Versioned inventory

`config/governance/external-channel-assets.v1.json` is the source of truth for repository asset disposition.

Top-level contract:

- `schema`: exactly `nexus.external-channel-retirement.inventory.v1`;
- `inventory_version`: immutable semantic identifier for this inventory revision;
- `audited_main_sha`: exact main SHA used to establish the baseline;
- `discovery_tokens`: exactly the case-sensitive markers the checker scans;
- `production_roots`: path prefixes where wildcard classification is forbidden;
- `allowed_historical_glob_roots`: path prefixes where controlled globs are permitted;
- `rules`: exact-path or glob rules, never both.

Each rule contains:

- `path` or `glob`;
- `asset_type`;
- `disposition`;
- `owner`;
- `rationale`;
- `write_surface`;
- `stop_new_writes_required`;
- `prerequisites`.

Allowed dispositions are:

- `active_compatibility` — still intentionally callable/readable during migration;
- `historical_evidence` — retained as non-runtime evidence;
- `data_migration_dependency` — required for historical schema/data interpretation;
- `safe_to_remove` — no intended long-term authority, but removal still requires its prerequisites;
- `retirement_control` — inventory, checker, tests, workflow and decommission documentation.

No disposition authorizes deletion, deployment or production mutation.

### 2. Discovery and validation checker

`scripts/ci/check_external_channel_retirement.py` uses only the Python standard library.

It will:

1. load JSON with duplicate-key rejection;
2. reject unknown or missing fields;
3. validate bounded identifiers, owners, rationale and prerequisites;
4. reject duplicate rules and overlapping matches;
5. reject globs under production roots;
6. require write surfaces to use exact paths and set `stop_new_writes_required=true`;
7. enumerate tracked files using `git ls-files -z`;
8. skip binary or oversized files safely;
9. discover files containing any configured token;
10. require every discovered file to match exactly one rule;
11. require every exact-path rule to exist and contain a discovery token;
12. emit only a bounded summary containing counts, schema/version and a digest, never source contents or discovered payloads.

Failure output is a bounded reason code plus safe path metadata. It does not print file contents, tokens from secrets, customer data or raw payloads.

### 3. Test boundary

`scripts/ci/tests/test_check_external_channel_retirement.py` tests the real checker against temporary repositories and manifests. Tests cover:

- a valid exact production asset and historical glob;
- uncovered references;
- overlapping rules;
- forbidden production wildcard rules;
- malformed/unknown schema fields;
- invalid write-surface declarations;
- stale exact inventory paths;
- deterministic bounded summary generation.

Tests are written before the checker implementation. The RED condition is the missing checker module; GREEN requires both focused tests and a scan of the actual repository.

### 4. Permanent CI gate

`.github/workflows/external-channel-retirement-gate.yml` runs on relevant pull requests, pushes to main and manual dispatch. It executes:

- Python syntax compilation;
- focused checker tests;
- the repository inventory scan.

The workflow installs no application dependencies and performs no network, database, Provider, outbound or deployment action.

### 5. Human decommission record

`docs/engineering/external-channel-decommission.md` explains the classifications, current risky write surfaces, migration phases, evidence required before deletion and rollback.

The phases are:

1. inventory and reintroduction gate — this slice;
2. prove caller/traffic/data-read state;
3. stop new legacy writes and migrate callers;
4. observe compatibility usage for a defined window;
5. remove safe code/configuration surfaces;
6. rehearse data/schema removal under #532;
7. execute separately authorized destructive migration, if still required.

## Production-path policy

Production roots include application code, deploy configuration, executable scripts, frontend code and GitHub workflows. A wildcard cannot classify references in these roots because that would allow a new runtime dependency to enter unnoticed. Every production-root reference must therefore receive an explicit exact-path decision.

Historical globs are permitted only for bounded roots such as `docs/`, `backend/tests/` and `backend/alembic/versions/`. A new matching production file will fail the gate until an explicit rule and review are added.

## Error handling and fail-closed behavior

The checker exits non-zero for malformed JSON, unsupported schema, invalid rules, missing tracked files, uncovered references, overlapping rules, forbidden globs, invalid write-surface declarations or repository enumeration failures.

It never silently falls back to an empty inventory. It does not swallow exceptions in the control path. Unexpected failures map to a bounded `inventory_check_failed` result while preserving a traceback only in the GitHub Actions job log when Python itself fails.

## Security and privacy

- No source-file contents are emitted.
- No live credential or endpoint value is stored in the inventory.
- No customer, tracking, contact, address, Provider payload or tool payload is read into evidence beyond local token detection.
- Summary output is low-cardinality and digest-bound.
- The workflow has read-only repository permissions.
- The slice has no database, outbound, Provider, deployment or external-resource side effects.

## Compatibility and rollback

All files are additive. Reverting the PR removes the inventory and gate without changing runtime behavior or historical data. The rollback does not reactivate ExternalChannel; the existing disabled/compatibility behavior remains exactly as it was on the starting main.

## Acceptance boundary for this slice

This slice is accepted when:

- current discovered assets have an owner and disposition;
- production references require exact-path classification;
- write-capable legacy surfaces are explicit;
- new unclassified references fail CI;
- tests and actual repository scan pass on the exact head;
- no current WhatsApp/Provider behavior is modified;
- no migration or destructive cleanup is introduced.

This is meaningful progress toward #572 but does not close the Work Item. Stop-new-writes migration, caller removal, observation evidence and any #532-backed destructive migration remain separate reviewable delivery slices.

## Spec self-review

- Placeholder scan: no TBD/TODO or deferred implementation ambiguity exists.
- Consistency: the manifest, checker, tests, workflow and documentation share one schema and one non-destructive acceptance boundary.
- Scope: one independently testable inventory/control slice; runtime migration and schema deletion are explicitly excluded.
- Ambiguity: production roots require exact paths; historical roots alone may use globs; write surfaces are always exact and flagged.