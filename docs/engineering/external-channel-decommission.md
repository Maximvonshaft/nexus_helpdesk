# ExternalChannel Decommission

## Status

ExternalChannel is a retired transport. Production configuration already rejects enablement, but compatibility code, historical persistence contracts, disabled settings, frontend labels, scripts, tests, migrations and reports remain in the repository.

Work Item #572 owns the complete decommission. PR #597 delivers only the first non-destructive slice: an authoritative repository inventory and a permanent reintroduction gate. It does not authorize runtime removal, database mutation, deployment or production deletion.

## Current authority

The machine-readable authority is:

- `config/governance/external-channel-assets.v1.json`
- schema: `nexus.external-channel-retirement.inventory.v1`
- checker: `scripts/ci/check_external_channel_retirement.py`
- required check: `external-channel-retirement-gate`

The inventory supports:

- one exact `path`;
- a grouped list of exact `paths` when every asset shares the same owner, disposition and prerequisites;
- a controlled historical `glob` under an approved non-runtime root.

Grouped `paths` are expanded into individual exact rules before evaluation. They do not weaken production-path enforcement.

## Asset dispositions

| Disposition | Meaning | Removal authority |
|---|---|---|
| `active_compatibility` | A current consumer, API, worker, frontend or workflow still imports or exposes compatibility behavior. | Requires consumer migration and observation evidence. |
| `historical_evidence` | Report, documentation or test evidence with no runtime authority. | May be archived or removed only through separately reviewed repository hygiene work. |
| `data_migration_dependency` | Model, schema, enum, migration or bootstrap needed to interpret historical data. | Requires data-read proof and #532 recovery rehearsal before destructive change. |
| `safe_to_remove` | No intended long-term authority, but prerequisites are not yet proven. | Requires the listed prerequisites and a bounded removal PR. |
| `retirement_control` | Manifest, checker, tests and CI workflow that enforce this process. | Retained until #572 is fully closed and successor controls are accepted. |

## Known write-capable surfaces

The inventory explicitly marks legacy write activation and compatibility call paths. The initial baseline includes:

- service-package bootstrap that installs the unresolved-event persistence patch;
- legacy bridge persistence helpers;
- unresolved-event storage;
- admin and ticket compatibility APIs;
- operator replay paths;
- background jobs;
- message-dispatch compatibility calls.

This classification is intentionally conservative. A write-capable classification means the file can directly or indirectly invoke obsolete persistence behavior. It does not mean the path is currently receiving production traffic.

Every write-capable entry must:

- be an exact path, never a glob;
- set `write_surface=true`;
- set `stop_new_writes_required=true`;
- list `caller_migration` as a prerequisite;
- remain blocked from removal until traffic and observation evidence exist.

## Gate behavior

The checker fails closed when:

- JSON contains duplicate keys;
- the schema, fields or identifiers are invalid;
- an exact inventory path is no longer tracked;
- an exact inventory path no longer contains a discovery marker;
- a tracked marker-bearing file has no rule;
- a file matches multiple rules;
- a wildcard can classify a production-capable root;
- a historical wildcard matches no current marker-bearing file;
- a write surface is not exact or lacks the stop-new-writes control;
- Git tracked-file enumeration or source reading fails.

Success output contains only bounded counts, the audited main SHA, inventory version and a SHA-256 digest. It does not emit source contents, customer data, tracking/contact/address data, Provider payloads, tool payloads, credentials or endpoint values.

## Decommission phases

### Phase 1 — inventory and reintroduction gate

Delivered by PR #597:

1. classify current repository assets;
2. identify known write-capable surfaces;
3. prevent new unclassified references;
4. establish exact-head CI evidence.

No runtime behavior changes in this phase.

### Phase 2 — caller, traffic and data-read evidence

Before changing behavior:

1. enumerate every import, API consumer, worker entry point and frontend consumer;
2. prove whether compatibility routes receive traffic;
3. measure legacy-table write/read activity using bounded operational evidence;
4. confirm historical rows remain readable through approved models and migrations;
5. identify retention, legal and audit requirements;
6. record a rollback-compatible migration map.

Absence of a code-search caller is not sufficient evidence of zero runtime traffic.

### Phase 3 — stop new legacy writes

A separate PR must:

1. add failing tests for each write path;
2. migrate callers to current governed channel boundaries;
3. remove bootstrap monkey patching and write helpers only after callers are migrated;
4. preserve historical read compatibility;
5. prove WhatsApp/Provider routing, governed outbound and operator workflows are unchanged;
6. add operational counters or bounded audit evidence for attempted legacy writes.

The phase must fail closed rather than silently dropping events.

### Phase 4 — compatibility observation

After stop-new-writes deployment is separately authorized:

1. observe route and legacy-store activity for a defined window;
2. investigate every non-zero call or write;
3. prove no required consumer depends on the compatibility surface;
4. verify rollback remains possible;
5. retain exact release and evidence identifiers.

The observation window and acceptance threshold must be defined in the implementing Work Item or PR. This document does not invent production thresholds.

### Phase 5 — remove safe code and configuration

Remove only assets whose prerequisites are proven. Keep the inventory updated in the same PR. The gate must remain green on the exact head.

Expected candidates include disabled-only settings, legacy bridge helpers, obsolete scripts and misleading frontend labels. Each removal must preserve current governed WhatsApp/Provider behavior.

### Phase 6 — rehearse destructive data/schema change

Any table, column or historical-row deletion is blocked on #532. Required evidence includes:

- production-like backup and restore rehearsal;
- row-count and integrity reconciliation;
- historical read-path validation;
- rollback timing and operator procedure;
- migration upgrade and downgrade behavior where downgrade is safe;
- explicit retention/legal approval where applicable.

### Phase 7 — separately authorized destructive migration

Destructive migration is optional, not presumed. It occurs only if retained historical structures no longer provide required business, audit or recovery value and all #532 gates are accepted.

## WhatsApp and Provider non-regression boundary

ExternalChannel retirement must not become a backdoor refactor of current channel execution. In every later phase:

- customer-visible messages remain governed;
- WhatsApp and Provider routing remain configuration-driven;
- current outbound policy and controlled execution boundaries remain authoritative;
- no real outbound is triggered by tests or migration tooling;
- no Provider enablement, credential change or production mutation is implied by a green repository gate.

## Review checklist for later slices

- exact current main and current #572 claim re-read;
- one current PR for the active slice;
- all affected inventory entries updated;
- failing tests precede behavior changes;
- caller and traffic evidence attached in bounded form;
- migration and rollback evidence explicit;
- no unresolved blocking review thread;
- required checks green on the exact final head;
- #572 remains open until all acceptance criteria are completed and merged.

## Rollback

PR #597 is additive. Reverting it removes the inventory, checker, tests, workflow and explanatory documents. It does not change runtime code, persisted data, database schema, active routes, Provider configuration or outbound behavior.

Later behavior-changing slices require their own rollback plan. Re-enabling the retired transport is not an acceptable rollback strategy.
