# ExternalChannel Decommission Inventory Gate — Implementation Plan and Execution Record

> **Workflow authority:** Superpowers brainstorming, writing-plans, test-driven-development, requesting-code-review and verification-before-completion.

**Goal:** Establish a versioned, machine-verifiable disposition for every current ExternalChannel repository asset and fail closed when any new path or content reference is unclassified.

**Starting main:** `e761bcfd9850451bcba7ffe679c4b83f87ec91bf`

**Work Item:** #572 (`NEX-AUD-014`), first non-destructive slice only.

**Contract:** `nexus.external-channel-retirement.inventory.v1`

**Tech stack:** Python 3.11 standard library, `unittest`, JSON, Git index metadata and GitHub Actions.

## Safety constraints

- No runtime route, model, table, migration, WhatsApp/Provider path or production configuration is changed.
- No deployment, customer traffic, real outbound, Provider call, credential operation, production-data mutation or destructive deletion is authorized.
- The PR references #572 and does not close it.
- Historical data removal remains blocked on caller/data evidence, observation and #532 rehearsal.
- Evidence output is bounded; source contents, customer data, credentials and Provider/tool payloads are not emitted.

## Final design contract

### Discovery markers

The checker requires the exact ordered marker set:

```json
[
  "ExternalChannel",
  "external_channel",
  "EXTERNAL_CHANNEL",
  "externalChannel",
  "external-channel"
]
```

A regular tracked file is a discovered asset when either its repository path or its non-binary contents contain a configured marker. Path matching prevents empty or unusual-encoding named assets from bypassing the gate.

### Git object boundary

Tracked files are enumerated with:

```bash
git -C <repo> ls-files -z --stage
```

Only regular blobs with mode `100644` or `100755` are scanned. Symlinks (`120000`) and gitlinks/submodules (`160000`) are excluded explicitly rather than opened as local files. Unknown modes fail closed.

### Inventory selectors

Every rule uses exactly one selector:

- `path`: one exact tracked path;
- `paths`: a group of exact tracked paths sharing the same metadata, expanded into individual exact rules before evaluation; or
- `glob`: a controlled historical pattern under an approved non-runtime root.

Production-capable paths cannot be classified by a glob. Every discovered path must match exactly one expanded rule. Every exact rule must remain tracked and marker-bearing; every historical glob must match at least one current discovered path.

### Write-surface controls

A write-capable asset must:

- use an exact selector;
- set `write_surface=true`;
- set `stop_new_writes_required=true`;
- include `caller_migration` in its prerequisites.

### Workflow trigger boundary

The lightweight gate runs on every pull request to `main`, every push to `main`, and manual dispatch. It does not use path filters because an unanticipated directory is itself a relevant reintroduction vector.

## Execution checklist

### Task 1 — Design and claim

- [x] Re-read current main, #489 coordination rules, #545, #572, open Work Items, open PR manifests and current ExternalChannel code/search surface.
- [x] Publish and re-read the winning `## AGENT_CLAIM` on #572.
- [x] Create one branch from the exact audited main SHA.
- [x] Write the design before production implementation.
- [x] Define explicit exclusions for runtime migration and destructive cleanup.

### Task 2 — Establish TDD RED

- [x] Create `scripts/ci/tests/test_check_external_channel_retirement.py` before the checker module.
- [x] Define strict manifest, coverage, overlap, stale-path, write-control and bounded-summary behavior.
- [x] Commit the expected missing-module RED state before production checker code.
- [x] Add later regression tests before grouped-selector, git-index, marker-variant and path-discovery fixes.

### Task 3 — Implement strict parser and evaluator

- [x] Reject duplicate JSON keys.
- [x] Reject unknown/missing top-level and rule fields.
- [x] Validate schema, version, audited SHA, identifiers, POSIX paths and bounded text.
- [x] Expand grouped exact paths and reject duplicate selectors.
- [x] Reject globs that could classify a production root.
- [x] Enforce exact one-rule coverage.
- [x] Reject stale exact rules and stale historical globs.
- [x] Enforce write-surface prerequisites.
- [x] Build deterministic bounded summary evidence with a canonical inventory SHA-256.

### Task 4 — Implement repository discovery

- [x] Parse Git stage records and scan only regular tracked blobs.
- [x] Exclude gitlinks and symlinks explicitly.
- [x] Detect all five naming variants in both paths and contents.
- [x] Treat binary contents as non-searchable while still detecting marker-bearing paths.
- [x] Fail closed on Git enumeration, index-format, file-type and read failures.

### Task 5 — Populate inventory and permanent gate

- [x] Create `config/governance/external-channel-assets.v1.json`.
- [x] Assign owners, dispositions, rationales and prerequisites.
- [x] Mark known legacy write-capable services and compatibility callers.
- [x] Keep application, deploy, workflow and frontend assets exact.
- [x] Bound historical globs to approved documentation, test and migration roots.
- [x] Add `.github/workflows/external-channel-retirement-gate.yml` with read-only repository permissions.
- [x] Run the gate for every main PR/push rather than a fallible directory allowlist.
- [x] Upload a bounded result artifact for both success and failure diagnosis.
- [x] Add phased decommission, non-regression and rollback guidance.

### Task 6 — Evidence-driven scan convergence

The real repository scan was used as the authority rather than a manual search claim.

- [x] First scan exposed `vendor/chatwoot` as a Git `160000` gitlink; add a regression test and parse Git index modes correctly.
- [x] Next scan exposed `.gitignore`; classify its retired local-environment compatibility references exactly.
- [x] Next scan exposed `webapp/e2e/smoke.spec.ts`; add a bounded historical E2E test root and rule.
- [x] Code-quality review identified missing lower-camel and hyphen marker variants; add tests and expand the marker contract.
- [x] Code-quality review identified content-only discovery; add a path-marker regression test and path/content discovery.
- [x] Code-quality review identified path-filter trigger bypass; run the full-tree gate on every main change.
- [ ] Obtain a successful full repository scan on the final head after all review fixes.

### Task 7 — Final review and handoff

- [ ] Run Python syntax compilation on the exact final head.
- [ ] Run all focused unit tests on the exact final head and record the exact test count.
- [ ] Run the actual tracked-repository inventory scan on the exact final head.
- [ ] Confirm all repository-required workflows are green on that same SHA.
- [ ] Re-read PR changed files and prove no runtime/model/migration file changed.
- [ ] Re-read unresolved review threads and mergeability.
- [ ] Complete a line-by-line specification-compliance review.
- [ ] Complete a code-quality review with no Critical or Important finding left open.
- [ ] Update the PR body with exact-head evidence and mark it ready for review.
- [ ] Update #572 to `In Review` without closing the Work Item.

## Verification commands

```bash
python -m py_compile scripts/ci/check_external_channel_retirement.py
python -m unittest -v scripts.ci.tests.test_check_external_channel_retirement
python scripts/ci/check_external_channel_retirement.py \
  --repo-root . \
  --manifest config/governance/external-channel-assets.v1.json
```

The GitHub Actions run on the exact PR head is the authoritative execution environment because it checks the complete tracked repository.

## Acceptance boundary for this slice

The slice is ready for review only when fresh exact-head evidence proves:

- all configured marker variants in paths and contents are classified;
- every production-capable reference is exact after group expansion;
- known write surfaces are explicit and migration-gated;
- unclassified, ambiguous, stale or malformed inventory state fails closed;
- the gate runs for every main change;
- success evidence is bounded;
- all seven changed files remain additive governance/test/documentation assets;
- current WhatsApp/Provider behavior, database schema and production data are unchanged.

The following remain separate #572 slices:

1. active caller, traffic and historical data-read evidence;
2. stop-new-writes migration;
3. compatibility observation window;
4. safe code and configuration removal;
5. #532-backed rehearsal before any destructive data/schema action.
