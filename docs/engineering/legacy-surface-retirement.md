# Legacy Surface Retirement

## Purpose

This document defines how Nexus distinguishes obsolete code from compatibility, protected history and current versioned contracts. The machine-readable authority is `config/governance/legacy-surface-domains.v1.json`.

## Current posture

The registry covers bounded known marker classes. It is not a claim that every unused symbol or duplicate algorithm in the repository has been proven dead. Domain owners must still provide consumer, traffic, data and rollback evidence before deletion.

## Commands

Validate the registry and scan the tracked tree:

```bash
python scripts/ci/check_legacy_surface_registry.py \
  --repo-root . \
  --registry config/governance/legacy-surface-domains.v1.json
```

Run focused tests:

```bash
python -m unittest -v \
  scripts.ci.tests.test_check_legacy_surface_registry \
  scripts.ci.tests.test_legacy_surface_version_contract
python -m py_compile \
  scripts/ci/check_legacy_surface_registry.py \
  scripts/ci/tests/test_check_legacy_surface_registry.py \
  scripts/ci/tests/test_legacy_surface_version_contract.py
```

## Result interpretation

- `ok=true`, `classification_complete=true`: all declared discovery markers have an allowed owner in this registry revision.
- `ok=false`, `unowned_count>0`: a declared marker exists without an allowed domain owner.
- `ok=false`, `overlap_count>0`: a marker resolves to multiple owners where the discovery rule requires one.
- exit `2`: registry, Git index or input evidence is malformed; treat the scan as unavailable.
- `findings_truncated=true`: the full count is retained but the output list is capped.

Registry path globs are case-insensitive; exact paths remain exact. Optional domain `path_regexes` are compiled during registry validation and should be root-anchored to their intended contract locations. Content-marker reads request at most the configured byte limit plus one sentinel byte.

The output never includes matched source lines or values. A finding contains a repository path, a truncated SHA-256 path fingerprint, the discovery rule and reason codes.

## Protected classes

### Alembic revisions

Files under `backend/alembic/versions/` are migration history. Date, round or `v2` tokens do not make them removable. Squashing requires a separate approved strategy plus empty-database upgrade, restore and rollback evidence under #532.

### Versioned contracts

Files such as `*.v1.json`, `*.v2.json` and `*.v10.json` may be current machine contracts. Removal requires a consumer inventory, replacement contract and explicit compatibility decision.

### Reachable Git history

Git history is not cleaned as ordinary source and is intentionally outside the tracked-tree registry. No tracked placeholder may masquerade as history evidence. #565 owns secret/exposure assurance, credential rotation and any explicitly authorized rewrite.

## Domain routing

- ExternalChannel: #572.
- Legacy static frontend: #573.
- Round reports, smoke scripts and workflows: #574.
- Application/Settings composition: #570.
- Release identity and legacy worker: #549.
- Lite API and Knowledge version-naming discovery: #650.

## Deletion protocol

A domain owner may delete an asset only after:

1. current-main consumer/reference proof;
2. runtime/traffic/data evidence where applicable;
3. migration and observation prerequisites;
4. focused negative and regression tests;
5. build/release compatibility evidence;
6. rollback instructions;
7. exact-head independent review.

The central registry never turns `safe_to_remove` into automatic deletion.

## Remote Skill Evidence

- `superpowers_using`: ADOPT for skill selection, planning and isolated branch execution.
- `github_actions_hardening`: ADAPT; no workflow is added, but future integration requirements are recorded.
- `secpriv`: ADOPT for bounded content reads and redacted evidence.
- `superpowers_verification`: ADOPT; no completion claim without exact-head test evidence.
- External scripts executed: none.
- Production/customer/Provider data accessed: none.
