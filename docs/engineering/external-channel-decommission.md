# ExternalChannel Decommission

## Status

ExternalChannel is a retired transport. Production configuration rejects enablement, but compatibility code, historical persistence contracts, disabled settings, frontend labels, scripts, tests, migrations and reports remain in the repository.

Work Item #572 owns the complete decommission. PR #680 is the current non-destructive inventory and reintroduction-control slice. It supersedes closed, unmerged PR #597 after that historical head became 135 commits behind and its source branch was unavailable. Neither PR authorizes runtime deletion, database mutation, deployment or production cleanup.

Current reconciliation baseline:

- audited main: `e96dac837b4f02a297602259953d7c274b9c2063`;
- inventory version: `2026-07-13.2`;
- current PR: #680;
- migration/schema/runtime changes: none.

## Current authority

The machine-readable authority is:

- inventory: `config/governance/external-channel-assets.v1.json`;
- schema: `nexus.external-channel-retirement.inventory.v1`;
- checker: `scripts/ci/check_external_channel_retirement.py`;
- focused tests: `scripts/ci/tests/test_check_external_channel_retirement.py`;
- CI workflow/check: `external-channel-retirement-gate`.

The workflow runs for every pull request to `main`, every push to `main`, and manual dispatch. It is intentionally not path-filtered: a new reference in an unanticipated directory must not bypass classification. Repository administrators still own branch-protection configuration outside this repository.

## Discovery and classification contract

The checker recognizes five marker variants:

- `ExternalChannel`;
- `external_channel`;
- `EXTERNAL_CHANNEL`;
- `externalChannel`;
- `external-channel`.

A regular tracked file is discovered when a marker appears in its repository path or non-binary contents. Git entries are enumerated through `git ls-files -z --stage`; only regular `100644` and `100755` blobs are scanned. Symlinks and gitlinks are excluded explicitly, while malformed index records and unsupported modes fail closed.

Inventory selectors are limited to:

- one exact `path`;
- grouped exact `paths` sharing one owner, disposition and prerequisite set;
- a controlled historical `glob` under an approved non-runtime root.

Grouped paths expand into individual exact rules before evaluation. Production-capable roots cannot be covered by globs.

## Asset dispositions

| Disposition | Meaning | Removal authority |
|---|---|---|
| `active_compatibility` | A current consumer, API, worker, frontend, registry or workflow still exposes compatibility behavior. | Requires consumer migration and observation evidence. |
| `historical_evidence` | Documentation, report or test evidence with no runtime authority. | Archive or remove only through separately reviewed hygiene work. |
| `data_migration_dependency` | Model, schema, enum, migration or bootstrap needed to interpret historical data. | Requires data-read proof and #532 recovery rehearsal before destructive change. |
| `safe_to_remove` | No intended long-term authority, but prerequisites are not yet proven. | Requires the listed prerequisites and a bounded removal PR. |
| `retirement_control` | Manifest, checker, tests and CI workflow enforcing this process. | Retain until #572 is closed or a successor control is accepted. |

## Current write-capable surfaces

The July 13 current-main reconciliation removed stale inventory entries for assets already deleted from the repository:

- `backend/app/services/external_channel_unresolved_store.py`;
- `backend/app/services/external_channel_payload_hash.py`.

It also removed `backend/app/services/__init__.py` from the inventory because the current file no longer contains an ExternalChannel marker or installs the retired unresolved-store patch.

The remaining explicitly classified write-capable surfaces are:

- `backend/app/services/external_channel_bridge.py`;
- `backend/app/api/admin.py`;
- `backend/app/api/operator_queue.py`;
- `backend/app/api/tickets.py`;
- `backend/app/services/background_jobs.py`;
- `backend/app/services/message_dispatch.py`.

The bridge still contains compatibility persistence helpers, including unresolved-event and attachment writes. This classification is conservative: it proves code capability, not current production traffic. Each write-capable rule is exact, sets `write_surface=true`, requires `stop_new_writes_required=true`, and lists caller migration before removal.

## Current cross-domain and release compatibility

The cross-domain registry `config/governance/legacy-surface-domains.v1.json` and its live contract test `scripts/ci/tests/test_check_legacy_surface_registry.py` explicitly coordinate the `external_channel_compatibility` domain and #572 ownership.

Current RC and worker configuration also keep the retired transport explicitly disabled:

- `deploy/.env.rc-test.example`;
- `deploy/docker-compose.operations-dispatch.yml`;
- `scripts/release/generate_rc_test_env.py`.

These files are classified as deployment compatibility, not as active transport authority. Their disabled settings may be removed only after the same caller-migration and observation prerequisites used for other compatibility configuration.

## Gate behavior

The checker fails closed when:

- JSON contains duplicate keys;
- schema, fields, identifiers or selectors are invalid;
- Git index data is malformed or contains an unsupported mode;
- an exact inventory path is not tracked;
- an exact path no longer contains a discovery marker;
- a discovered file has no rule;
- a discovered file matches multiple rules;
- a glob can classify a production-capable root;
- a historical glob matches no current reference;
- a write surface is not exact or lacks stop-new-writes controls;
- tracked-file enumeration or file reading fails.

Success output is bounded to aggregate counts, inventory metadata and a SHA-256 digest. It does not emit paths, source contents, customer data, Provider payloads, credentials, endpoints or raw operational payloads.

## Decommission phases

### Phase 1 — current repository inventory and reintroduction gate

PR #680 owns the current-main reconciliation of this phase:

1. classify every currently discovered reference;
2. identify remaining write-capable surfaces;
3. prevent new unclassified path or content references;
4. produce exact-head bounded CI evidence.

No runtime behavior changes in this phase.

### Phase 2 — caller, traffic and data-read evidence

Before changing behavior:

1. enumerate every import, API consumer, worker entry point and frontend consumer;
2. prove whether compatibility routes receive traffic;
3. measure legacy-table write/read activity through bounded operational evidence;
4. confirm historical rows remain readable through approved models and migrations;
5. identify retention, legal and audit requirements;
6. record a rollback-compatible migration map.

Code search alone is not evidence of zero runtime traffic.

### Phase 3 — stop new legacy writes

A separate PR must add failing tests for every write path, migrate callers to governed channel boundaries, preserve historical reads, and prove WhatsApp/Provider routing and customer-visible governance remain unchanged. Attempted legacy writes must fail closed or enter an explicit repair path; they must not be silently dropped.

### Phase 4 — compatibility observation

After separately authorized deployment of stop-new-writes behavior, define an observation window, investigate every non-zero call/write, verify rollback, and retain exact release/evidence identifiers. This document does not invent production thresholds.

### Phase 5 — remove safe code and configuration

Remove only assets whose prerequisites are proven. Update the inventory in the same PR and keep the exact-head gate green. Disabled settings, bridge helpers, obsolete scripts and misleading UI labels are candidates, not pre-authorized deletions.

### Phase 6 — rehearse destructive data/schema change

Any table, column or historical-row deletion is blocked on #532. Required evidence includes production-like backup/restore, row and integrity reconciliation, historical read validation, rollback timing, migration upgrade/downgrade behavior where safe, and retention/legal approval where applicable.

### Phase 7 — separately authorized destructive migration

Destructive migration is optional. It proceeds only if historical structures no longer provide required business, audit or recovery value and all #532 gates are accepted.

## WhatsApp and Provider non-regression boundary

ExternalChannel retirement must not become a backdoor refactor of current channel execution:

- customer-visible messages remain governed;
- WhatsApp and Provider routing remain configuration-driven;
- controlled execution and outbound policy remain authoritative;
- tests and migration tooling cannot send real outbound;
- a green repository gate does not enable a Provider, change credentials or mutate production.

## Review checklist for later slices

- re-read exact current main, #572 and active Claims;
- keep one current PR for the active slice;
- update every affected inventory entry;
- write failing tests before behavior changes;
- attach bounded caller/traffic/data-read evidence;
- state migration and rollback evidence explicitly;
- resolve every blocking review thread;
- require exact-final-head checks;
- keep #572 open until all acceptance criteria are completed and merged.

## Rollback

PR #680 is additive. Reverting it removes the inventory, checker, tests, workflow and explanatory documents without changing runtime code, persisted data, database schema, active routes, Provider configuration or outbound behavior. Later behavior-changing slices require their own rollback plans; re-enabling the retired transport is not an acceptable rollback strategy.
