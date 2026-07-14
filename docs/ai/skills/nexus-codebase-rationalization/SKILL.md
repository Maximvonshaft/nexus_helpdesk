---
name: nexus-codebase-rationalization
description: Evidence-backed repository archaeology, canonical implementation convergence, and permanent deletion of dead, duplicate, superseded, unsafe, or misleading Nexus code and assets.
version: 1.0.0
owner: nexus_osr_engineering_governance
work_item: 744
---

# Nexus Codebase Rationalization

## Purpose

Use this skill when Nexus has accumulated overlapping implementations, AI-generated patches, legacy compatibility paths, stale reports, dead configuration, duplicate UI or API authorities, or code whose production role is unclear.

The objective is not cosmetic cleanup. The objective is to leave one explicit canonical implementation per capability and permanently delete everything proven obsolete, while preserving behavior, data integrity, security boundaries, rollback, and accepted product contracts.

## Authority

Apply authority in this order:

1. Explicit, scope-specific user authorization. A broad cleanup request does not by itself override a fail-closed domain blocker, protected history, data-migration prerequisite, or separately owned destructive Work Item.
2. The cross-cutting legacy registry at `config/governance/legacy-surface-domains.v1.json` and its owner #650 for tracked-tree domain ownership, deletion authorization and protected history.
3. Current `main`, Issue #489, and the active domain Work Item and comments.
4. Current PR reviews, checks, migrations, tests, deployment contracts, and exact runtime behavior.
5. Nexus product, architecture, security, privacy, and delivery contracts.
6. This skill.

This skill never authorizes production deployment, production-data mutation, live outbound/provider actions, credential access, or destructive database operations.

## Required operating model

- Never write directly to `main`.
- Claim the owning Work Item before branch, file, or PR writes.
- Use one current branch and PR per Work Item.
- Re-read `main`, the Work Item, current PRs, migrations, source, tests, deployment paths, and the cross-cutting legacy registry before each destructive slice.
- Keep deletion slices vertically coherent and independently reviewable.
- Do not mix feature expansion, broad redesign, framework replacement, or dependency-major upgrades into rationalization work.
- Do not create a second cross-cutting domain registry; rationalization inventories are execution ledgers under the existing authority.
- Do not interpret general pressure to delete as permission to bypass a named domain owner or unresolved fail-closed prerequisite.

## Mandatory dispositions

Every candidate path or implementation must receive exactly one disposition:

| Disposition | Meaning | Required action |
|---|---|---|
| `CANONICAL` | Accepted surviving implementation | Protect with ownership and architecture checks |
| `DUPLICATE_DELETE` | Same capability is implemented elsewhere | Migrate callers and permanently delete |
| `DEAD_DELETE` | No supported consumer or execution path exists | Permanently delete after evidence gate |
| `SUPERSEDED_DELETE` | Replaced by an accepted newer authority | Prove replacement parity and permanently delete |
| `LEGACY_ACTIVE_MIGRATE_THEN_DELETE` | Still reachable or data-bearing | Migrate, observe, verify, then permanently delete |
| `COMPATIBILITY_WITH_DEADLINE` | Temporary compatibility is still required | Record owner, deadline, replacement, counters, and exit test |
| `GENERATED_OR_VENDOR_MANAGED` | Generated, vendored, or externally managed | Do not hand-edit; fix source or generation contract |
| `UNKNOWN_BLOCK_DELETE` | Evidence is insufficient | Do not delete; create a named investigation obligation |

`UNKNOWN_BLOCK_DELETE` is not a permanent resting state. It must identify the missing evidence, owner, and next verification action.

## Deletion evidence gate

A source deletion is allowed only when all applicable evidence is recorded:

1. **Exact baseline** — current `main` SHA and branch head.
2. **Static consumers** — imports, references, symbols, route registrations, templates, generated links, configuration keys, tests, scripts, workflows, and docs.
3. **Runtime consumers** — startup, worker queues, background jobs, API/router registration, CLI entry points, scheduled jobs, feature flags, fallback paths, and dynamic imports.
4. **Build and deployment consumers** — Docker, Compose, packaging, Vite/build inputs, static delivery, release scripts, migrations, smoke tests, and rollback paths.
5. **Historical reason** — commit/PR/Issue evidence explaining why the implementation exists and whether the reason remains valid.
6. **Replacement authority** — canonical destination, behavioral parity, caller migration, and compatibility policy.
7. **Data and schema impact** — row ownership, historical reads, migration history, backup, repair, downgrade/rollback, and re-upgrade where applicable.
8. **Security/privacy impact** — authentication, authorization, tenant/country/channel/role isolation, PII, audit evidence, logging, and secret exposure.
9. **Verification** — focused tests plus all applicable repository gates on one exact head.
10. **Rollback** — Git reversal for source-only deletion; explicit backup/restore rehearsal for data/schema work.

Absence of a text reference is not sufficient proof of dead code. Dynamic registration, configuration, reflection, runtime string lookup, data compatibility, and deployment consumers must be checked explicitly.

## Workflow

### Phase 1 — Reconstruct authority and entry points

Read and record:

- repository and Work Item governance;
- the cross-cutting legacy registry and domain owner;
- product/design authority for affected UI paths;
- backend application startup and router registration;
- worker and scheduled-job entry points;
- configuration and environment loading;
- database models and migration head;
- frontend route and build entry points;
- Docker/Compose/release/smoke/rollback paths;
- active PRs and recent commits touching the target area.

Produce an entry-point map before deleting runtime code.

### Phase 2 — Build the implementation inventory

For every capability, record:

- capability name and existing domain owner;
- canonical path;
- alternative/legacy paths;
- active callers and data dependencies;
- current disposition;
- deletion prerequisites;
- owning Work Item;
- proof links or exact code locations.

Prefer machine-readable YAML or JSON plus a concise human summary. Treat this output as an execution ledger; it must reference rather than replace the cross-cutting legacy registry.

### Phase 3 — Choose one canonical implementation

When multiple implementations exist:

- select the implementation that matches current product/runtime authority;
- reject selection based only on recency, file size, or stylistic preference;
- migrate every supported caller;
- add characterization tests before behavior-preserving migration;
- fail closed rather than silently falling back to a retired implementation;
- remove the obsolete implementation in the same bounded program, not as an indefinite follow-up.

### Phase 4 — Execute a vertical deletion slice

For one coherent capability or asset group:

1. Establish focused RED/characterization evidence where behavior exists.
2. Migrate or remove consumers.
3. Delete source, tests, configuration, deployment references, and misleading docs together when safe.
4. Run focused verification after each atomic change.
5. Run full applicable gates on the final exact head.
6. Record deleted paths, replacement, proof, and residual risk in the PR.

Do not perform a repository-wide blind delete or one giant rewrite.

### Phase 5 — Prevent reintroduction

Add the smallest effective permanent gate, such as:

- forbidden import/path checks;
- route-to-Href parity tests;
- duplicate frontend/design-system authority checks;
- retired marker inventories;
- unused-setting detection;
- circular dependency checks;
- architecture boundary tests;
- CODEOWNERS or explicit ownership;
- deprecation deadline validation.

A deletion program is incomplete when the removed authority can be silently recreated.

## Asset-specific rules

### Root reports, scratch files, and temporary manifests

Delete when they are not an accepted runbook, product/security contract, migration artifact, workflow input, linked evidence, or active operator instruction. Volatile delivery evidence belongs in Issues, PRs, or bounded artifacts, not as accumulating repository-root reports.

### Runtime code

Do not delete until all static, dynamic, startup, worker, route, fallback, configuration, and deployment consumers are proven absent or migrated.

### Frontend

`webapp/` and the accepted product/design authority govern survival. Deletion of `frontend/`, legacy styles, routes, or components remains owned by #573 and requires route, authorization, accessibility, degraded-state, build, and deployment parity.

### Retired channel compatibility

Retirement remains owned by #572. Historical reads, active writers, caller migration, observation, recovery, and migration evidence must complete before final code/schema deletion.

### Application composition and settings

Modularization and dead-setting removal remain owned by #570. Preserve route/header/runtime behavior through characterization tests.

### Database models and migrations

Do not delete or rewrite historical Alembic migrations as repository hygiene. Schema/data removal requires an owning migration Work Item, synthetic PostgreSQL evidence, backup/restore, downgrade/rollback, and re-upgrade verification.

### Tests

Do not delete tests merely because they fail or cover legacy behavior. Delete or rewrite them only after the corresponding product/runtime contract is retired or migrated and equivalent critical-path coverage exists.

### Generated and vendored content

Do not manually clean generated output or vendor code. Change the source, generator, pin, or inclusion policy; then regenerate or remove through the governing contract.

## AI-generated code controls

Before adding any file, component, service, hook, model, route, configuration key, or utility:

- search for an existing canonical implementation;
- identify the owning capability and boundary;
- do not create a parallel abstraction to avoid understanding the current one;
- do not preserve old and new implementations without an owner and deletion deadline;
- do not use `any`, silent fallback, broad exception swallowing, duplicated state, or copy-pasted adapters to make migration appear complete;
- update callers and delete superseded code in the bounded program;
- document why a new authority is necessary.

## Required outputs

Each audit or deletion PR must include:

- exact baseline and head SHA;
- selected skill roles and authority read;
- implementation inventory or updated slice;
- deletion ledger with dispositions;
- deleted paths and line/file counts;
- canonical replacements and migrated callers;
- tests and commands with results;
- data/security/deployment/rollback assessment;
- unresolved `UNKNOWN_BLOCK_DELETE` items;
- explicit statement that production authority remains unchanged unless separately granted.

## Completion rule

Do not claim repository cleanup complete because lint, build, tests, or CI are green. Completion requires accepted exact-head evidence that the targeted capability has one canonical authority, every supported consumer is migrated, obsolete code is permanently deleted, reintroduction is blocked, and the owning Work Item is accepted and merged.
