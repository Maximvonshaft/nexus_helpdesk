# Legacy Surface Registry Design

## Status

- Work Item: #650
- Baseline: `main@7f39ff7b215db8092fc0c7c0d48b289171b54e45`
- Delivery class: non-destructive inventory and governance control
- Runtime, schema, migration, frontend, deployment and workflow changes: none

## Problem

Nexus carries several visually similar but operationally different classes of old-looking material:

1. retired runtime/configuration compatibility;
2. duplicate or transitional authorities;
3. round-specific delivery evidence;
4. historical migrations required for database reconstruction;
5. current versioned machine contracts whose `v1`/`v2` names are not deprecation signals.

Deleting by filename, version token or age would create migration, compatibility and release risk.

## Decision

Introduce `nexus.legacy-surface.registry.v1` as a cross-domain routing registry. It does not duplicate domain inventories. It records the authoritative Issue, disposition and deletion prerequisites for each domain and gives the scanner enough bounded selectors to classify known high-confidence legacy markers.

The first slice uses `fail_closed` only for the bounded discovery rules declared in the registry. It does not claim semantic dead-code proof for every tracked file.

## Authority boundaries

- #549: release identity, worker and business readiness.
- #565: reachable Git history and exposure assurance.
- #570: application bootstrap and Settings decomposition.
- #572: ExternalChannel compatibility retirement.
- #573: legacy frontend and design-system retirement.
- #574: workflow/smoke and historical artifact retirement.
- #532: destructive migration and recovery rehearsal.
- #650: cross-domain registry, protected classes, unowned marker routing, Lite API and Knowledge version-naming convergence discovery.

## Data model

Each domain has:

- one stable ID;
- one positive owner Issue;
- one allowed disposition;
- `deletion_authorized=false`;
- rationale and prerequisites;
- exact, glob or path-scoped content selectors;
- references to authoritative Issues, PRs or documents.

Discovery rules are separate from domain selectors. A discovery rule identifies a high-confidence legacy marker and declares which domain IDs are allowed to own it. This separation permits an orphan marker to fail closed instead of silently assigning ownership.

## Security and privacy

The scanner:

- reads only Git-index regular files;
- excludes symlinks and gitlinks;
- caps text reads at 256 KiB;
- skips binary/non-UTF-8 content;
- emits no source lines or matched values;
- emits bounded repository paths and truncated path hashes;
- has no network, credential, Provider, database or production-data access;
- performs no writes outside stdout.

SecPriv disposition: no personal-data source or external sink is introduced. Repository content remains semi-trusted; result artifacts are bounded and do not copy it.

## GitHub Actions hardening disposition

No workflow is added in this slice because #574 owns workflow-graph convergence. When integrated later, the workflow must use an unprivileged `pull_request` trigger, `contents: read`, immutable action SHAs, `persist-credentials: false`, no untrusted `${{ }}` interpolation in shell, and bounded artifact retention.

## Deletion rule

`safe_to_remove` is evidence classification, not authorization. Deletion still requires:

1. authoritative owner Issue acceptance;
2. reference and consumer proof;
3. migration/observation prerequisites;
4. focused regression and build evidence;
5. rollback plan;
6. exact-head review and merge.

## Rollback

Revert the additive registry/checker/test/docs commit. No database, Provider, deployment, customer communication or external-resource cleanup is required.
