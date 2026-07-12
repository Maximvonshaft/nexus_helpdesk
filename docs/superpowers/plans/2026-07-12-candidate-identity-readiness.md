# Controlled Test Candidate Identity and Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make a controlled test candidate report its exact release identity, fail readiness on migration/identity drift, and remain externally safe by default.

**Architecture:** Extend the existing Settings and release-metadata authority rather than creating a parallel release-profile subsystem. `/readyz` combines existing process checks with bounded release-metadata and Alembic identity checks. Candidate examples and helper containers remain opt-in for Provider/outbound connectivity. The isolated RC generator resolves and validates the complete tracked Alembic graph before starting containers.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, AST-based migration graph inspection, pytest, GitHub Actions, Docker Compose environment contracts.

## Global Constraints

- Work Item authority: #549.
- Base: `main@9ae6e9f6aa3742e8576dbe7270a6f17d691dc312`.
- Release class: controlled test deployment, not production GO.
- No migrations, schema changes, Provider calls, outbound, deployment, tag or production mutation.
- Preserve development behavior when candidate identity enforcement is disabled.
- Evidence output must remain bounded and contain no secrets or database URL.

---

### Task 1: Witness the identity and safe-default gaps

**Files:**
- Create: `backend/tests/test_candidate_readiness_identity.py`
- Modify: `backend/tests/test_candidate_compose_contract.py`

**Interfaces:**
- Consumes: current `Settings`, `app.version`, `/readyz`, candidate env, Runtime warmer and sidecar defaults.
- Produces: failing behavioral contracts for Tasks 2–4.

- [x] Add tests requiring `APP_VERSION`, `EXPECTED_MIGRATION_HEAD` and metadata enforcement settings.
- [x] Add `/readyz` mismatch, missing metadata, exact match and development compatibility cases.
- [x] Change the candidate environment contract to require disabled Provider/outbound defaults.
- [x] Add no-secret/no-network Runtime warmer tests for disabled authority and kill-switch rollback.
- [x] Add mock/no-autostart sidecar defaults to the candidate environment contract.
- [x] Record RED evidence from candidate contract failure and independent exact-head review.

### Task 2: Add Settings identity controls

**Files:**
- Modify: `backend/app/settings.py`
- Test: `backend/tests/test_candidate_readiness_identity.py`

**Interfaces:**
- Produces:
  - `Settings.app_version: str`
  - `Settings.expected_migration_head: str | None`
  - `Settings.readiness_require_release_metadata: bool`

- [x] Load `APP_VERSION`, defaulting to bounded development/unknown identity.
- [x] Load a trimmed optional `EXPECTED_MIGRATION_HEAD`.
- [x] Load `READINESS_REQUIRE_RELEASE_METADATA`, defaulting to true in production and false otherwise.
- [x] Make readiness require an expected migration head when candidate metadata enforcement is enabled.
- [x] Run focused Settings/readiness tests through Backend CI.

### Task 3: Bind FastAPI identity and `/readyz`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_candidate_readiness_identity.py`

**Interfaces:**
- Consumes: Task 2 Settings fields and existing `runtime_identity_status()`.
- Produces: bounded readiness fields `migration`, `release_metadata_ready`, and `reason_codes`.

- [x] Replace the historical FastAPI version literal with `settings.app_version`.
- [x] Calculate expected/observed migration readiness without exposing connection details.
- [x] Treat release metadata as ready when complete or when enforcement is disabled.
- [x] Return 503 for required missing/mismatched migration identity or incomplete required release metadata.
- [x] Preserve existing storage, frontend and signing checks and their logs.
- [x] Preserve `migration_revision` for compatibility while adding structured migration identity.

### Task 4: Make the candidate example and helper containers safe and exact

**Files:**
- Modify: `deploy/.env.candidate.example`
- Modify: `scripts/smoke/warm_private_ai_runtime.py`
- Test: `backend/tests/test_candidate_compose_contract.py`
- Test: `backend/tests/test_candidate_readiness_identity.py`

**Interfaces:**
- Produces: explicit migration identity and safe-by-default external-authority/connectivity flags.

- [x] Add `EXPECTED_MIGRATION_HEAD=<alembic-head>`.
- [x] Add `READINESS_REQUIRE_RELEASE_METADATA=true`.
- [x] Default Private AI Runtime authority off and canary percentage to zero.
- [x] Gate Runtime warmer before credential reads or HTTP requests unless Runtime is enabled, canary is positive and kill switch is false.
- [x] Default native WhatsApp and outbound dispatch off.
- [x] Default sidecar connector to mock and auto-start accounts to empty.
- [x] Run exact-head candidate environment, warmer and sidecar contract tests.

### Task 5: Bind the isolated RC generator to the complete tracked migration graph

**Files:**
- Modify: `scripts/release/generate_rc_test_env.py`
- Modify: `scripts/release/tests/test_generate_rc_test_env.py`

**Interfaces:**
- Consumes: literal `revision` and `down_revision` assignments in `backend/alembic/versions/*.py`.
- Produces: exact `EXPECTED_MIGRATION_HEAD` in the generated isolated RC environment.

- [x] Record RC failure evidence: observed head `20260711_0058`, expected head missing, `/readyz` reason `migration_head_required`.
- [x] Add tests for unique-head discovery, multiple-head rejection, malformed revision rejection, disconnected-cycle rejection and shell-loaded output.
- [x] Parse migrations with `ast` without importing/executing revision files.
- [x] Validate unique revision IDs and all parent references.
- [x] Require exactly one current Head, reject reachable cycles, and require every revision to be reachable from that Head.
- [x] Write the validated Head to `EXPECTED_MIGRATION_HEAD`.
- [ ] Re-run isolated RC and confirm expected/observed migration identity matches.

### Task 6: Wire, verify and deliver

**Files:**
- Modify: `.github/workflows/backend-ci.yml`
- Create: `docs/superpowers/specs/2026-07-12-candidate-identity-readiness-design.md`
- Create: `docs/superpowers/plans/2026-07-12-candidate-identity-readiness.md`

**Interfaces:**
- Produces: exact-head CI evidence and reviewable rollback contract.

- [x] Add the focused readiness test to the existing production-drift test group; do not create a new workflow.
- [x] Confirm the workflow diff changes only the explicit pytest path list.
- [ ] Run Backend CI, Full Regression, PostgreSQL Migration, Production Readiness, RC Candidate, Candidate WhatsApp, Release Image, Security, Knowledge, Smoke and Integration checks.
- [ ] Obtain independent review on the final exact head and resolve every actionable thread.
- [ ] Re-read current main, require `0 behind`, and merge only with expected Head SHA.
- [ ] Update #549 with accepted evidence; keep full business readiness and production GO open.
