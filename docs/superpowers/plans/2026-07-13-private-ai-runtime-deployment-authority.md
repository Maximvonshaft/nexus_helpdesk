# Private AI Runtime Deployment Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the first strict repository-owned deployment-manifest authority for #591 without pretending that the live Private AI Runtime is already reproducible.

**Architecture:** Add an isolated `infra/private-ai-runtime/` boundary containing a JSON Schema, then implement a standard-library fail-closed validator in `scripts/ci/`. The validator references #586 capability authority, binds exact deployment identities, separates immutable release files from non-authoritative mutable state, and emits bounded redacted evidence. No deploy runner, workflow or live-host action is added.

**Tech Stack:** Python 3.11 standard library, pytest, JSON Schema 2020-12, GitHub pull-request CI.

## Global Constraints

- Work Item: #591; bounded first slice only.
- Base: `main@e96dac837b4f02a297602259953d7c274b9c2063`.
- No database or Alembic migration.
- No GitHub Actions workflow change.
- No live A10 access, deployment, Provider enablement, real outbound, model replacement or production-data mutation.
- #586 remains capability-contract authority; #582 remains traffic authority; #533 remains release authority.
- Secrets remain external references and must never appear in findings.
- TDD RED must be observed before implementation; exact-head CI and independent review remain mandatory.

---

### Task 1: Define the failing deployment-contract tests

**Files:**
- Create: `scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py`

**Interfaces:**
- Consumes: planned script path `scripts/ci/check_private_ai_runtime_deployment_manifest.py`.
- Produces: expected functions `validate_manifest(raw)`, `build_validation_result(raw, finding_limit=20)`, constants `REQUIRED_TOP_LEVEL`, `MAX_MANIFEST_BYTES`, and CLI arguments `--manifest`, `--output`, `--finding-limit`.

- [x] **Step 1: Write a valid manifest fixture**

Include exact source/tree hashes, capability-contract hash, immutable artifacts, derived mutable paths, external secret reference, digest-pinned image, model revision/hash, service definition, acceptance, rollback and drift.

- [x] **Step 2: Add fail-closed behavior tests**

Cover unknown keys, mutable tags, hash errors, path overlap, authority escalation, string commands, destructive rollback, schema mismatch, secret fields, duplicates, service hash mismatch, embedded secret arguments and oversized input.

- [x] **Step 3: Run RED**

```bash
python -m pytest -q scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py
```

Expected at test-only head: failures rooted in the missing validator/schema, with tests collecting successfully.

- [x] **Step 4: Commit RED**

```bash
git add scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py
git commit -m "test(runtime): define deployment manifest contract"
```

### Task 2: Add the strict JSON Schema

**Files:**
- Create: `infra/private-ai-runtime/deployment-manifest.v1.schema.json`

**Interfaces:**
- Consumes: test fixture and #586 schema name `nexus.ai_runtime.capabilities.v1`.
- Produces: `$id=nexus.private_ai_runtime.deployment_manifest.v1` and exact required top-level field set.

- [x] **Step 1: Define exact top-level structure**

Use JSON Schema 2020-12, `additionalProperties=false`, all fifteen required fields and reusable definitions for hashes, IDs, paths, artifacts, state, secrets, images, models, services and argv commands.

- [x] **Step 2: Encode non-negotiable constants**

Set Linux/NVIDIA host family, capability schema, digest-pinned images, non-authoritative mutable state, non-destructive rollback and fail-closed drift.

- [x] **Step 3: Keep cross-field checks in Python**

JSON Schema documents shape. Service/artifact hash equality, root overlap, exact release differences and safe secret-store schemes remain Python validator responsibilities.

### Task 3: Implement the minimal fail-closed validator

**Files:**
- Create: `scripts/ci/check_private_ai_runtime_deployment_manifest.py`
- Test: `scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py`

**Interfaces:**
- Produces: `ManifestValidationError`, `validate_manifest`, `build_validation_result`, `main`.
- Produces result schema: `nexus.private_ai_runtime.deployment_manifest_validation.v1`.

- [x] **Step 1: Implement bounded parsing**

Read at most `1_048_576 + 1` bytes, reject oversized input before JSON parsing and normalize read/encoding/JSON failures to bounded reason codes.

- [x] **Step 2: Implement primitives**

Validate exact key sets, non-empty strings, booleans, bounded integers, unique string lists, lowercase SHA-1/SHA-256 identities, IDs, canonical absolute/relative POSIX paths and argv arrays.

- [x] **Step 3: Reject inline secrets**

Recursively reject secret-value field names. Reject command arguments beginning with token/password/secret/API-key/private-key flags and Authorization headers. Never echo input values.

- [x] **Step 4: Validate deployment sections**

Validate source, capability reference, host profile, immutable artifacts, mutable state, secret references, images, models, services, acceptance, rollback and drift.

- [x] **Step 5: Enforce cross-field invariants**

Reject mutable/secret/rollback/result paths under immutable root; reject service definitions absent from artifacts or with mismatched hashes; reject same-release rollback; require all six acceptance checks.

- [x] **Step 6: Emit bounded validation evidence**

Return only result schema, boolean status, canonical manifest hash, validated source commit, total finding count and bounded `{code,path}` findings. Write atomically through a temporary file.

- [x] **Step 7: Run GREEN and compile**

```bash
python -m pytest -q scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py
python -m py_compile \
  scripts/ci/check_private_ai_runtime_deployment_manifest.py \
  scripts/ci/tests/test_check_private_ai_runtime_deployment_manifest.py
```

Expected: `21 passed`; compilation exits `0`.

### Task 4: Document authority and residual work

**Files:**
- Create: `infra/private-ai-runtime/README.md`
- Create: `docs/engineering/private-ai-runtime-deployment-authority.md`
- Create: `docs/superpowers/specs/2026-07-13-private-ai-runtime-deployment-authority-design.md`
- Create: `docs/superpowers/plans/2026-07-13-private-ai-runtime-deployment-authority.md`

**Interfaces:**
- Consumes: #591 acceptance and #586/#582/#532/#533 authority boundaries.
- Produces: operator-facing semantics, non-goals, validation command and explicit closure gaps.

- [x] **Step 1: Document what the schema proves**

State that it proves contract shape and exact identities only.

- [x] **Step 2: Document what remains unproven**

List live inventory, source reconstruction, real manifest, deploy/preflight/acceptance/backup/restore/rollback/drift runners, clean-host rebuild and exact-candidate evidence.

- [x] **Step 3: Document prohibited content and actions**

Exclude secrets, live addresses, customer/provider payloads, server-local state represented as authority, production actions and release GO.

### Task 5: Exact-head verification and delivery

**Files:**
- Modify only PR #683 and Issue #591 comments/metadata; no code path expansion.

**Interfaces:**
- Consumes: exact PR head and repository-required checks.
- Produces: bounded delivery evidence and honest lifecycle state.

- [ ] **Step 1: Re-read branch files and PR changed paths**

Confirm the diff contains only the seven claimed paths.

- [ ] **Step 2: Run exact-head CI**

Wait for repository checks on the exact implementation head. Do not infer success from local tests.

- [ ] **Step 3: Perform independent security/architecture review**

Review concrete trust boundaries, path validation, secret leakage, command representation, result redaction and authority overlap. Record only high-confidence findings.

- [ ] **Step 4: Update PR evidence**

Record exact head, RED/GREEN evidence, focused commands, check results, security review and remaining #591 gaps. Keep Draft until governance gates are met.

- [ ] **Step 5: Update Issue #591**

Record the delivered bounded slice and current PR. Do not close #591 because clean-host rebuild, recovery and rollback acceptance remain outstanding.

## Self-review

- Spec coverage: first-slice objective, invariants, security, tests, documentation and residual acceptance are mapped.
- Placeholder scan: no `TBD`, `TODO`, unspecified error handling or deferred test instructions.
- Type consistency: validator names, schemas, constants, CLI arguments and test expectations match across tasks.
