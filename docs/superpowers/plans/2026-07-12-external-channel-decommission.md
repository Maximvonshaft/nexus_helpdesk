# ExternalChannel Decommission Inventory Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a versioned, machine-verifiable inventory of every current ExternalChannel repository reference and fail CI when an unclassified production or historical reference is introduced.

**Architecture:** A strict JSON manifest classifies exact production paths and controlled historical globs. A standard-library Python checker validates the manifest, discovers tracked files containing ExternalChannel markers, enforces one-rule coverage and emits bounded evidence. Focused tests define the fail-closed contract before implementation, and an additive GitHub Actions workflow runs the tests and actual repository scan.

**Tech Stack:** Python 3.11 standard library, `unittest`, JSON, `git ls-files`, GitHub Actions YAML.

## Global Constraints

- Starting main is `e761bcfd9850451bcba7ffe679c4b83f87ec91bf`.
- Contract schema is exactly `nexus.external-channel-retirement.inventory.v1`.
- No database model, table, migration, runtime route, WhatsApp/Provider path or production configuration is changed by this slice.
- No deploy, production-data mutation, outbound, Provider call, credential operation or destructive deletion is authorized.
- Production-root references require exact-path rules; globs are allowed only under bounded historical roots.
- Every write-capable legacy asset must be an exact rule with `write_surface=true` and `stop_new_writes_required=true`.
- Checker output must be bounded and must not print source-file contents.
- The PR references #572 but does not close it.

---

### Task 1: Define the checker contract with failing tests

**Files:**
- Create: `scripts/ci/tests/test_check_external_channel_retirement.py`
- Test target: `scripts/ci/check_external_channel_retirement.py`

**Interfaces:**
- Consumes: a checker module loaded from the exact repository path.
- Produces: behavioral requirements for `parse_inventory(payload)`, `evaluate_inventory(inventory, tracked_paths, token_paths)` and `build_safe_summary(inventory, evaluation)`.

- [ ] **Step 1: Write the failing module-loader test and behavior fixtures**

Use `importlib.util.spec_from_file_location` to load the checker. Build temporary repository paths and strict manifest payloads with:

```python
VALID_BASE = {
    "schema": "nexus.external-channel-retirement.inventory.v1",
    "inventory_version": "test.1",
    "audited_main_sha": "a" * 40,
    "discovery_tokens": ["ExternalChannel", "external_channel", "EXTERNAL_CHANNEL"],
    "production_roots": ["backend/app/", "scripts/", ".github/workflows/"],
    "allowed_historical_glob_roots": ["docs/", "backend/tests/", "backend/alembic/versions/"],
    "rules": [],
}
```

Add tests asserting:

```python
def test_valid_inventory_covers_exact_production_and_historical_glob(): ...
def test_uncovered_reference_fails_closed(): ...
def test_overlapping_rules_fail_closed(): ...
def test_production_glob_is_forbidden(): ...
def test_unknown_top_level_field_is_rejected(): ...
def test_write_surface_requires_exact_stop_new_writes_control(): ...
def test_stale_exact_rule_is_rejected(): ...
def test_safe_summary_is_deterministic_and_contains_no_paths(): ...
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
python -m unittest -v scripts.ci.tests.test_check_external_channel_retirement
```

Expected: import failure because `scripts/ci/check_external_channel_retirement.py` does not exist.

- [ ] **Step 3: Commit RED evidence**

```bash
git add scripts/ci/tests/test_check_external_channel_retirement.py
git commit -m "test(governance): define ExternalChannel retirement gate"
```

---

### Task 2: Implement strict manifest parsing and rule validation

**Files:**
- Create: `scripts/ci/check_external_channel_retirement.py`
- Test: `scripts/ci/tests/test_check_external_channel_retirement.py`

**Interfaces:**
- Produces:
  - `InventoryError(reason: str, detail: str | None = None)`
  - `InventoryRule` frozen dataclass
  - `Inventory` frozen dataclass
  - `parse_inventory(payload: object) -> Inventory`
  - `load_inventory(path: Path) -> Inventory`

- [ ] **Step 1: Implement minimal strict parser**

The parser must:

```python
SCHEMA = "nexus.external-channel-retirement.inventory.v1"
ALLOWED_DISPOSITIONS = {
    "active_compatibility",
    "historical_evidence",
    "data_migration_dependency",
    "safe_to_remove",
    "retirement_control",
}
```

Reject duplicate JSON keys using `object_pairs_hook`, reject unknown fields, validate a 40-character lowercase hexadecimal audited SHA, require three unique non-empty discovery tokens, normalize POSIX paths, reject absolute paths and `..`, require exactly one of `path` or `glob`, and validate bounded owner/rationale/prerequisite values.

A glob rule is valid only when its static prefix begins with one of `allowed_historical_glob_roots` and does not begin with a production root. A write surface must use `path`, not `glob`, and must set `stop_new_writes_required=true`.

- [ ] **Step 2: Run focused parser tests**

```bash
python -m unittest -v scripts.ci.tests.test_check_external_channel_retirement
```

Expected: parser tests pass; evaluation tests may still fail because coverage functions are not implemented.

- [ ] **Step 3: Commit parser implementation**

```bash
git add scripts/ci/check_external_channel_retirement.py scripts/ci/tests/test_check_external_channel_retirement.py
git commit -m "feat(governance): parse ExternalChannel retirement inventory"
```

---

### Task 3: Implement discovery, one-rule coverage and bounded evidence

**Files:**
- Modify: `scripts/ci/check_external_channel_retirement.py`
- Test: `scripts/ci/tests/test_check_external_channel_retirement.py`

**Interfaces:**
- Produces:
  - `list_tracked_files(repo_root: Path) -> tuple[str, ...]`
  - `discover_token_paths(repo_root: Path, tracked_paths: Sequence[str], tokens: Sequence[str]) -> tuple[str, ...]`
  - `evaluate_inventory(inventory: Inventory, tracked_paths: Sequence[str], token_paths: Sequence[str]) -> InventoryEvaluation`
  - `build_safe_summary(inventory: Inventory, evaluation: InventoryEvaluation) -> dict[str, object]`
  - `check_repository(repo_root: Path, manifest_path: Path) -> dict[str, object]`

- [ ] **Step 1: Implement tracked-file discovery**

Run `git -C <root> ls-files -z`, reject command failure, normalize and sort paths, skip files larger than 2 MiB and files containing NUL bytes, and decode text with UTF-8 replacement only for token detection. Do not return or print file contents.

- [ ] **Step 2: Implement exact one-rule evaluation**

For each token path:

```python
matches = [rule for rule in inventory.rules if rule.matches(path)]
if not matches:
    raise InventoryError("inventory_reference_uncovered", path)
if len(matches) != 1:
    raise InventoryError("inventory_reference_ambiguous", path)
```

For every exact rule, require the path to be tracked and token-bearing. Reject duplicate exact paths, duplicate globs and any rule whose pattern can classify a production-root path.

- [ ] **Step 3: Implement bounded summary**

Summary keys are exactly:

```python
{
    "ok": True,
    "schema": inventory.schema,
    "inventory_version": inventory.inventory_version,
    "audited_main_sha": inventory.audited_main_sha,
    "tracked_file_count": len(tracked_paths),
    "reference_file_count": len(token_paths),
    "exact_rule_count": ...,
    "glob_rule_count": ...,
    "write_surface_count": ...,
    "disposition_counts": {...},
    "inventory_sha256": ...,
}
```

Do not include path names, source text or token values.

- [ ] **Step 4: Run all focused tests and syntax compilation**

```bash
python -m py_compile scripts/ci/check_external_channel_retirement.py
python -m unittest -v scripts.ci.tests.test_check_external_channel_retirement
```

Expected: all focused tests pass with no warning or traceback.

- [ ] **Step 5: Commit GREEN implementation**

```bash
git add scripts/ci/check_external_channel_retirement.py scripts/ci/tests/test_check_external_channel_retirement.py
git commit -m "feat(governance): enforce ExternalChannel inventory coverage"
```

---

### Task 4: Populate the audited repository inventory

**Files:**
- Create: `config/governance/external-channel-assets.v1.json`
- Test: actual repository scan through the checker.

**Interfaces:**
- Consumes: exact current repository paths and the checker schema.
- Produces: one disposition and owner for every current token-bearing tracked file.

- [ ] **Step 1: Add exact production rules**

Create exact rules for active application, API, schema/model, settings, executable script, deploy, frontend and workflow paths that contain one of the discovery tokens. Classify each as `active_compatibility`, `safe_to_remove` or `retirement_control` based on current behavior.

Mark known write-capable assets explicitly, including the legacy bridge, unresolved-event persistence and bootstrap patch surfaces. Their prerequisites must include caller migration and an observation/evidence gate.

- [ ] **Step 2: Add controlled historical rules**

Use bounded globs only under:

```text
docs/
backend/tests/
backend/alembic/versions/
```

Root-level historical reports require exact paths or a separately bounded root-report rule approved by the checker; no `**/*` catch-all is allowed.

- [ ] **Step 3: Run the actual repository scan**

```bash
python scripts/ci/check_external_channel_retirement.py \
  --repo-root . \
  --manifest config/governance/external-channel-assets.v1.json
```

Expected: one JSON line with `"ok": true`; no source paths or source contents in the success output.

If the checker reports `inventory_reference_uncovered`, inspect only the safe path, classify it explicitly, rerun, and do not weaken the production-root exact-path rule.

- [ ] **Step 4: Commit audited inventory**

```bash
git add config/governance/external-channel-assets.v1.json
git commit -m "chore(governance): inventory retired ExternalChannel assets"
```

---

### Task 5: Add permanent CI and engineering decommission guidance

**Files:**
- Create: `.github/workflows/external-channel-retirement-gate.yml`
- Create: `docs/engineering/external-channel-decommission.md`
- Modify: `config/governance/external-channel-assets.v1.json` to classify the new control files.

**Interfaces:**
- Produces: permanent `external-channel-retirement-gate` check and a human migration/rollback record.

- [ ] **Step 1: Add read-only workflow**

The workflow must use:

```yaml
permissions:
  contents: read
```

It runs on pull requests to `main`, pushes to `main`, and manual dispatch. Relevant paths include the inventory, checker/tests, ExternalChannel-named application/config/script paths, migration/tests/docs, and the workflow itself.

Commands:

```bash
python -m py_compile scripts/ci/check_external_channel_retirement.py
python -m unittest -v scripts.ci.tests.test_check_external_channel_retirement
python scripts/ci/check_external_channel_retirement.py --repo-root . --manifest config/governance/external-channel-assets.v1.json
```

- [ ] **Step 2: Add engineering document**

Document:

- current compatibility and write-capable classes;
- why the gate is non-destructive;
- phase ordering from inventory to #532-backed schema removal;
- evidence required before caller removal;
- WhatsApp/Provider non-regression boundary;
- rollback by reverting the additive PR.

- [ ] **Step 3: Re-run focused and actual checks**

```bash
python -m py_compile scripts/ci/check_external_channel_retirement.py
python -m unittest -v scripts.ci.tests.test_check_external_channel_retirement
python scripts/ci/check_external_channel_retirement.py --repo-root . --manifest config/governance/external-channel-assets.v1.json
```

Expected: all pass, bounded output only.

- [ ] **Step 4: Commit CI and documentation**

```bash
git add .github/workflows/external-channel-retirement-gate.yml docs/engineering/external-channel-decommission.md config/governance/external-channel-assets.v1.json
git commit -m "ci(governance): block ExternalChannel reintroduction"
```

---

### Task 6: Review, exact-head verification and handoff

**Files:**
- Review all files in this plan; no additional product files are expected.

**Interfaces:**
- Produces: review evidence, exact-head check status and an explicit partial-slice handoff for #572.

- [ ] **Step 1: Perform specification-compliance review**

Confirm every design requirement maps to code/test/manifest/workflow evidence and that the PR does not modify runtime behavior, models, migrations, WhatsApp/Provider routing or production data.

- [ ] **Step 2: Perform code-quality review**

Review strict parsing, path normalization, glob restrictions, bounded output, deterministic sorting/digesting, test isolation and error reason codes. Critical or high findings block progress.

- [ ] **Step 3: Verify exact PR head**

Require the new workflow plus repository-required checks to complete successfully on the same head SHA. Re-read unresolved review threads and mergeability after checks finish.

- [ ] **Step 4: Update #572 without false closure**

Record the delivered inventory/gate slice, exact head and remaining work:

- prove active caller/traffic/data-read state;
- stop new writes and migrate callers;
- observe compatibility usage;
- remove safe code/configuration;
- rehearse any destructive schema/data action under #532.

The PR body uses `Refs #572`, not `Closes #572`.

## Plan self-review

- Spec coverage: every component, failure mode, security boundary and rollback requirement has a task.
- Placeholder scan: no TBD/TODO, “similar to” or unspecified validation step remains.
- Type consistency: parser/evaluator/discovery/summary function names are stable across tasks.
- Scope: one independently reviewable and reversible control slice; runtime migration and destructive cleanup remain outside this plan.