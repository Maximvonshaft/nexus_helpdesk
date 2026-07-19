---
name: nexus-codebase-rationalization
description: Evidence-backed repository archaeology, canonical implementation convergence, and permanent deletion of dead, duplicate, superseded, unsafe, or misleading Nexus code and assets.
version: 2.0.0
owner: nexus_osr_engineering_governance
---

# Nexus codebase rationalization

## Purpose

Use this skill when Nexus contains overlapping implementations, stale delivery reports, dead configuration, duplicate UI or API authorities, compatibility paths or code whose runtime role is unclear.

The objective is one explicit canonical implementation per capability, with obsolete code physically removed and anti-reintroduction gates added. Cosmetic cleanup without caller, data, security and release evidence is not completion.

## Authority order

1. Explicit authorization for the exact action.
2. `config/architecture/compatibility-lifecycle.v1.json`.
3. `config/governance/legacy-surface-domains.v2.json` as subordinate current-tree discovery only.
4. Current GitHub repository objects and exact commit evidence.
5. Current product, architecture, security, privacy, migration and release contracts.
6. This skill.

A broad cleanup request does not by itself authorize destructive production-data or schema retirement. Source deletion, deployment, Provider enablement and destructive migration are separate actions.

## Operating model

- Never write directly to `main`.
- Use one branch and one PR for one convergence program.
- Read current source, callers, routes, workers, configuration, migrations, deployment paths and tests before deletion.
- Do not create a second registry, product, transport, permission model, Provider router, queue, lifecycle or verification chain.
- Do not store mutable PR, branch, SHA, workflow or delivery status in long-lived architecture files.
- Keep evidence outside the candidate tree and bind it to one unchanged source/tree identity.

## Required dispositions

| Disposition | Meaning | Required action |
|---|---|---|
| `CANONICAL` | Surviving authority | Protect with ownership and architecture checks |
| `DUPLICATE_DELETE` | Same capability exists elsewhere | Migrate callers and delete |
| `DEAD_DELETE` | No supported consumer or data dependency | Delete with negative tests |
| `SUPERSEDED_DELETE` | Replaced by accepted authority | Prove parity and delete |
| `MIGRATE_THEN_DELETE` | Still reachable or data-bearing | Migrate, observe, verify, then delete |
| `COMPATIBILITY_WITH_DEADLINE` | Temporary compatibility is required | Record owner, replacement, deadline and exit test |
| `GENERATED_OR_VENDOR_MANAGED` | Generated or externally managed | Fix source, generator, pin or inclusion policy |
| `UNKNOWN_BLOCK_DELETE` | Evidence is incomplete | Name the missing proof and owner; do not guess |

`UNKNOWN_BLOCK_DELETE` is not a permanent resting state.

## Deletion evidence gate

Before deleting an implementation or asset, establish all applicable evidence:

1. exact current source and tree identity;
2. static consumers: imports, references, routes, templates, configuration, tests, scripts and docs;
3. runtime consumers: startup, workers, jobs, dynamic registration, flags and fallbacks;
4. build/deployment consumers: Docker, Compose, packaging, static delivery, smoke and rollback;
5. replacement authority and caller migration;
6. data/schema effects, backup, repair, downgrade and re-upgrade;
7. security/privacy effects, authorization, tenant isolation, PII, audit and secrets;
8. focused tests and the complete Canonical Acceptance matrix;
9. rollback appropriate to the scope.

Text-search absence is not sufficient proof because dynamic registration, configuration, reflection and persisted data may still be consumers.

## Workflow

### 1. Reconstruct authority and entry points

Map product routes, backend routers, workers, configuration, migrations, deployment and current GitHub objects. Do not delete runtime code before this map exists.

### 2. Inventory implementations

For each capability record the canonical path, alternatives, active callers, data dependencies, disposition, deletion prerequisites and evidence.

### 3. Select one authority

Choose based on current product and runtime contracts, not recency or style. Migrate every supported caller and fail closed instead of retaining a silent fallback.

### 4. Execute a vertical slice

Migrate consumers, delete source/tests/configuration/deployment references/misleading docs together, and run focused verification after each coherent change.

### 5. Prevent reintroduction

Use the smallest permanent control that closes the root cause: forbidden path/import checks, route collision gates, transport authority checks, unused-setting detection, architecture tests or removal-deadline validation.

## Asset rules

- Root reports, scratch manifests and completed implementation plans belong in PRs/Issues or external artifacts, not the current source tree.
- `webapp/` is the sole authenticated operator product. The public WebChat widget is a separate customer surface.
- Runtime code is removable only after startup, route, worker, configuration and deployment consumers are gone.
- ExternalChannel persistence names are data-migration dependencies, not an active transport; destructive removal requires explicit migration authorization.
- Historical Alembic revisions are protected executable history, not ordinary residue.
- Versioned machine contracts are schema identities, not parallel implementations.
- Tests are rewritten or deleted only after the covered contract is migrated or retired.
- Generated/vendor content is changed through its source or inclusion policy.

## AI-generated code controls

Before adding a file, service, component, route, setting or helper:

- search for the existing canonical responsibility;
- identify the owning boundary;
- extend that authority rather than creating a parallel abstraction;
- avoid silent fallback, copy-pasted adapters, duplicate state and import-time monkey-patching;
- remove the superseded implementation in the same convergence program;
- document why a genuinely new authority is necessary.

## Completion rule

Completion requires one accepted exact Head with all callers migrated, obsolete code deleted, current governance files free of mutable delivery history, anti-reintroduction controls passing and no unresolved duplicate authority. Green CI alone does not authorize deployment or destructive production-data changes.
