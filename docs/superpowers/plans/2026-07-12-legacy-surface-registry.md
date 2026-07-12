# Legacy Surface Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-destructive, machine-readable cross-domain legacy-surface registry and bounded fail-closed checker without modifying runtime behavior.

**Architecture:** A strict JSON registry separates domain ownership from marker discovery. A standard-library Python checker validates the registry, reads Git-index regular files, classifies only bounded declared markers and emits deterministic redacted JSON evidence.

**Tech Stack:** JSON, Python 3.11 standard library, `unittest`, Git index.

## Global Constraints

- Baseline is `main@7f39ff7b215db8092fc0c7c0d48b289171b54e45`.
- Work Item is #650 and the active Claim owns only the six declared additive paths.
- Do not modify runtime, API, model, migration, frontend, deployment, workflow or production data.
- Do not duplicate #549/#565/#570/#572/#573/#574 domain authority.
- Do not add a GitHub Actions workflow until #574 releases workflow ownership.
- No source contents, credentials, customer data, Provider payloads or tool payloads may appear in scan output.
- `safe_to_remove` never authorizes deletion.

---

### Task 1: Define the registry contract

**Files:**
- Create: `config/governance/legacy-surface-domains.v1.json`
- Test: `scripts/ci/tests/test_check_legacy_surface_registry.py`

**Interfaces:**
- Produces: `nexus.legacy-surface.registry.v1`
- Consumes: Issue ownership and prerequisites from #549/#565/#570/#572/#573/#574/#532/#650.

- [ ] Write strict registry fixtures covering valid domains, duplicate IDs, protected dispositions and deletion authorization.
- [ ] Run `python -m unittest -v scripts.ci.tests.test_check_legacy_surface_registry` and confirm the initial import/contract assertions fail before implementation.
- [ ] Add the JSON registry with exact dispositions, owners, selectors and bounded discovery rules.
- [ ] Re-run the focused tests and confirm registry validation cases pass.
- [ ] Commit the contract and tests.

### Task 2: Implement bounded validation and scanning

**Files:**
- Create: `scripts/ci/check_legacy_surface_registry.py`
- Modify: `scripts/ci/tests/test_check_legacy_surface_registry.py`

**Interfaces:**
- Produces: `validate_registry(raw)`, `collect_tracked_files(repo_root)`, `scan_registry(registry, tracked_files, read_text=...)`, CLI schema `nexus.legacy-surface.scan-result.v1`.
- Consumes: `config/governance/legacy-surface-domains.v1.json`.

- [ ] Add failing tests for unowned markers, deterministic ordering, bounded findings, path fingerprints and symlink exclusion.
- [ ] Run the focused suite and record the expected RED failures.
- [ ] Implement strict schema validation with exact keys and fail-closed unknown domain references.
- [ ] Implement Git-index regular-file enumeration, bounded text reading and selector/discovery matching.
- [ ] Implement deterministic bounded JSON output without source content.
- [ ] Run `python -m unittest -v scripts.ci.tests.test_check_legacy_surface_registry` and require zero failures.
- [ ] Run `python -m py_compile scripts/ci/check_legacy_surface_registry.py scripts/ci/tests/test_check_legacy_surface_registry.py`.
- [ ] Run the checker against a temporary Git fixture and require exit 0 for classified markers and exit 1 for an orphan marker.
- [ ] Commit the checker.

### Task 3: Document authority and safe execution

**Files:**
- Create: `docs/superpowers/specs/2026-07-12-legacy-surface-registry-design.md`
- Create: `docs/engineering/legacy-surface-retirement.md`
- Create: `docs/superpowers/plans/2026-07-12-legacy-surface-registry.md`

**Interfaces:**
- Produces: operator/engineer guidance for future domain-owned cleanup.
- Consumes: registry contract and checker behavior.

- [ ] Document domain authority, protected classes and deletion prerequisites.
- [ ] Document scanner usage, result interpretation and workflow deferral to #574.
- [ ] Record selected remote skill versions and ADOPT/ADAPT/REJECT decisions.
- [ ] Scan the documents for placeholders and remove all `TBD`, `TODO`, `implement later` or ambiguous steps.
- [ ] Commit documentation.

### Task 4: Exact-head verification and Draft PR

**Files:**
- Verify all six claimed paths only.

**Interfaces:**
- Produces: reviewable Draft PR linked to #650.

- [ ] Compare the branch against current `main`; if `main` moved, integrate or explicitly mark the evidence stale.
- [ ] Run focused tests, compile, checker scan and `git diff --check` on the exact branch head where tooling permits.
- [ ] Review the checker under SecPriv: no source content or sensitive payload output.
- [ ] Review any future workflow design under GitHub Actions hardening; do not add a workflow in this slice.
- [ ] Open a Draft PR with coordination manifest, skill evidence, verification results, limitations and rollback.
- [ ] Keep #650 open; this slice does not authorize deletion or close the full convergence outcome.
