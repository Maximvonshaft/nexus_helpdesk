# Compatibility and retirement authority

## Purpose

Nexus distinguishes active authority, bounded compatibility, protected migration history and removable residue through one lifecycle authority:

- policy, owners, replacements and deadlines: `config/architecture/compatibility-lifecycle.v1.json`;
- subordinate current-tree discovery: `config/governance/legacy-surface-domains.v2.json`;
- enforcement: `scripts/ci/check_legacy_surface_registry.py`.

The subordinate registry has no stored commit SHA and no delivery status. Every scan derives its source identity from the Git checkout being verified.

## Command

```bash
python scripts/ci/check_legacy_surface_registry.py --repo-root .
```

Focused contract tests:

```bash
python -m unittest -v \
  scripts.ci.tests.test_check_legacy_surface_registry \
  scripts.ci.tests.test_legacy_surface_version_contract
```

## Result contract

- `ok=true`, `classification_complete=true`: every declared discovery marker has an allowed current owner.
- `ok=false`, `unowned_count>0`: a declared marker has no allowed owner.
- `ok=false`, `overlap_count>0`: a marker resolves to an unauthorized or ambiguous owner set.
- exit `2`: registry, Git index or bounded input evidence is malformed.
- `source_sha`: current checkout identity calculated at scan time.

Findings contain only path identity, a truncated path fingerprint, rule identity and reason codes. They contain no matched source values or customer/provider data.

## Protected non-residue

### Alembic revisions

`backend/alembic/versions/` is executable schema history required for deterministic empty-database upgrade, restore and rollback. It is not ordinary dead code. Removal or squashing requires an explicit migration strategy and destructive-retirement authorization.

### Versioned machine contracts

Files such as `*.v1.json`, `*.v2.json` and `*.v10.json` identify schema versions. A version token is not evidence of a parallel implementation.

### Public WebChat

`backend/app/static/webchat/` is the customer-facing channel surface. It is not a second authenticated operator product.

## Retirement protocol

A compatibility asset may be removed only after current consumer proof, runtime/data evidence where applicable, migration prerequisites, negative and regression tests, release compatibility evidence, rollback instructions and exact-head review. The registry never converts `safe_to_remove` into an automatic destructive action.

Completed implementation plans, stale PR pointers, branch names and historical test-round reports do not belong in current authority files and must not be reintroduced.
