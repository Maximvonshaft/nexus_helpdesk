# Controlled Test Candidate Identity and Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task by task and preserve exact-head evidence.

**Goal:** Make a controlled test candidate report its exact release identity, fail readiness on migration/identity drift, and remain externally safe by default without breaking existing production environments that have not adopted candidate migration binding.

**Architecture:** Extend the existing Settings and release-metadata authority rather than creating a parallel release-profile subsystem. `/readyz` combines existing process checks with independent release-metadata enforcement and opt-in exact Alembic binding. Candidate examples and helper containers remain opt-in for Provider/outbound connectivity. The isolated RC generator validates the complete tracked Alembic graph before starting containers, while runtime readiness validates the complete database version-row set.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, AST-based migration graph inspection, pytest, GitHub Actions and Docker Compose environment contracts.

## Global Constraints

- Work Item authority: #549.
- Release class: controlled test deployment, not production GO.
- No migrations, schema changes, Provider calls, outbound, deployment, tag or production mutation.
- Preserve existing production and development behavior when `EXPECTED_MIGRATION_HEAD` is not supplied.
- Candidate and isolated RC paths must always supply an expected Head.
- Evidence output must remain bounded and contain no secrets or database URL.

---

### Task 1: Witness identity and safe-default gaps

**Files:**
- Create: `backend/tests/test_candidate_readiness_identity.py`
- Modify: `backend/tests/test_candidate_compose_contract.py`

- [x] Require `APP_VERSION`, `EXPECTED_MIGRATION_HEAD` and release-metadata controls.
- [x] Add mismatch, incomplete metadata, exact match and development compatibility cases.
- [x] Prove existing production metadata enforcement does not implicitly require migration binding.
- [x] Add complete database Head-set behavior, including multiple-row fail-closed evidence.
- [x] Require disabled Provider/outbound defaults.
- [x] Require no-secret/no-network Runtime warmer behavior for disabled authority and kill-switch rollback.
- [x] Require mock/no-autostart sidecar defaults in both env and Compose fallback.
- [x] Record RED evidence where focused candidate contracts fail while Compose rendering and mock infrastructure remain healthy.

### Task 2: Add normalized identity controls

**Files:**
- Modify: `backend/app/settings.py`
- Test: `backend/tests/test_candidate_readiness_identity.py`

- [x] Load `APP_VERSION`, defaulting to bounded development/unknown identity.
- [x] Load optional `EXPECTED_MIGRATION_HEAD`.
- [x] Load `READINESS_REQUIRE_RELEASE_METADATA`, defaulting true in production and false otherwise.
- [x] Keep release-metadata enforcement independent from migration binding.
- [x] Activate exact migration binding only when `EXPECTED_MIGRATION_HEAD` is supplied.

### Task 3: Bind FastAPI identity and `/readyz`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_candidate_readiness_identity.py`

- [x] Replace historical FastAPI version literal with `settings.app_version`.
- [x] Query all `alembic_version` rows, not a scalar `LIMIT 1` value.
- [x] Fail closed on multiple observed database Heads.
- [x] Compare one complete observed Head to the expected candidate Head when binding is active.
- [x] Preserve the legacy scalar `migration_revision` only when exactly one Head exists.
- [x] Return bounded structured migration identity and reason codes.
- [x] Return 503 for migration mismatch/multiple Heads or incomplete required release metadata.
- [x] Preserve storage, frontend and signing checks.

### Task 4: Make candidate helpers safe by construction

**Files:**
- Modify: `deploy/.env.candidate.example`
- Modify: `deploy/docker-compose.candidate.yml`
- Modify: `scripts/smoke/warm_private_ai_runtime.py`
- Test: `backend/tests/test_candidate_compose_contract.py`
- Test: `backend/tests/test_candidate_readiness_identity.py`

- [x] Add candidate release identity and expected migration Head.
- [x] Default Runtime authority off and canary to zero.
- [x] Gate Runtime warmer before credential reads or HTTP unless Runtime is enabled, canary is positive and kill switch is false.
- [x] Default native WhatsApp and outbound dispatch off.
- [x] Default sidecar env to mock/no-autostart.
- [x] Default sidecar Compose fallback to mock/no-autostart even without an env override.

### Task 5: Bind isolated RC to the complete tracked migration graph

**Files:**
- Modify: `scripts/release/generate_rc_test_env.py`
- Modify: `scripts/release/tests/test_generate_rc_test_env.py`

- [x] Record the original RC failure: observed `20260711_0058`, expected missing, readiness rejected the candidate.
- [x] Parse literal migration metadata without importing revision modules.
- [x] Validate unique IDs and every parent reference.
- [x] Require one tracked Head, reject cycles and require every revision to be reachable.
- [x] Write the validated Head to `EXPECTED_MIGRATION_HEAD`.
- [x] Prove malformed, duplicate, unknown-parent, multiple-head, cyclic and disconnected graphs fail before startup.
- [ ] Re-run isolated RC on the final exact Head and confirm expected/observed identity matches.

### Task 6: Wire, verify and deliver

**Files:**
- Modify: `.github/workflows/backend-ci.yml`
- Create: `docs/superpowers/specs/2026-07-12-candidate-identity-readiness-design.md`
- Create: `docs/superpowers/plans/2026-07-12-candidate-identity-readiness.md`

- [x] Add focused readiness tests to the existing production-drift group; do not create a redundant workflow.
- [x] Keep the Backend workflow change to one explicit pytest path.
- [ ] Run exact-head Backend CI, Full Regression, PostgreSQL Migration, Production Readiness, RC Candidate, Candidate WhatsApp, Release Image, Security, Knowledge, Webapp, Smoke and Integration gates.
- [ ] Obtain independent final-head review and resolve every actionable thread.
- [ ] Re-read current `main`, require `0 behind`, and merge only with expected Head SHA.
- [ ] Record accepted evidence on #549 while keeping full release profiles, business readiness and production GO open.
