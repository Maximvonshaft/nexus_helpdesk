# Recovery Qualification Implementation Plan

> Required execution mode: Superpowers planning, TDD, systematic debugging and verification-before-completion.

**Goal:** Prove on current main that Nexus can perform a bounded migration rehearsal, create a verifiable PostgreSQL backup, restore it into a clean database and report explicit rollback outcomes without production effects.

**Work Item:** #532

**Baseline:** `main@7b46e48e5cd26fc400728cb84dfe04b57d5fa855`

## Global boundaries

- Disposable PostgreSQL only.
- No production backup/restore, data copy/deletion or DSAR.
- No deployment, image restart, release tag, Provider action or real outbound.
- Tenant retention/DSAR remains deferred to #546.
- Historical PR #608 is evidence only.

## Task 1 — Reproduce operator and evidence defects

**Files:**
- Create `scripts/qualification/recovery/test_recovery_contracts.py`
- Create `.github/workflows/osr-recovery-qualification.yml` in RED form

- Assert native URL separation.
- Assert explicit admin database authority before destructive setup.
- Assert temporary/archive/manifest/checksum/atomic `mv -T` backup behavior.
- Assert transactional fail-fast restore and explicit rollback states.
- Assert partial rollback status is written after image restart/health failure.
- Assert bounded snapshot/compare/RTO/RPO contracts.
- Run the dedicated gate and record the expected test-only failure.

## Task 2 — Correct operator backup and rollback

**Files:**
- Modify `scripts/deploy/backup_postgres.sh`
- Modify `scripts/deploy/rollback_release.sh`

- Normalize only known SQLAlchemy PostgreSQL prefixes to libpq form.
- Create a custom archive in a mode-restricted temporary bundle.
- Validate archive listing, one Alembic head, SHA-256, size and source identity.
- Atomically publish archive plus manifest with no-target-directory semantics.
- Verify manifest, digest, size, source identity and exact head before restore.
- Refuse in-place restore unless separately acknowledged.
- Use `pg_restore --exit-on-error --single-transaction`.
- Execute old-image restart only with an explicit health URL.
- Write structured success or partial-failure rollback states from an EXIT trap.

## Task 3 — Add bounded recovery evidence

**File:**
- Create `scripts/qualification/recovery/build_recovery_evidence.py`

- Snapshot one Alembic head, complete public table counts, marker and FK validation.
- Compare source and restore exactly.
- Calculate bounded RTO/RPO.
- Generate deterministic migration repair plans.
- Reject missing/multiple/invalid heads and unsafe digests.
- Keep reports below 256 KiB and free of business rows.

## Task 4 — Run disposable PostgreSQL rehearsal

**Files:**
- Create `scripts/qualification/recovery/run_recovery_qualification.sh`
- Expand `.github/workflows/osr-recovery-qualification.yml`

- Require explicit admin/source/restore URLs and recreate confirmation.
- Prove all URLs target one disposable cluster before `DROP DATABASE`.
- Use pgvector PostgreSQL 16.
- Upgrade, downgrade one revision, plan repair and re-upgrade.
- Seed a synthetic Market/Team relationship.
- Invoke the real backup script.
- Invoke the real rollback script against a clean restore database.
- Compare source/restore evidence and RTO/RPO.
- Remove backup bytes before artifact processing.
- Scan all JSON evidence before upload.
- Upload full evidence only after a clean scan; otherwise upload sanitized status only.
- Fail closed through one final gate.

## Task 5 — Document and verify

**Files:**
- Create `docs/ops/nexus-osr-recovery-qualification.md`
- Create `docs/superpowers/specs/2026-07-13-recovery-qualification-design.md`
- Create this plan

- Run dedicated exact-head recovery qualification on Alembic `20260713_0059`.
- Run all applicable repository checks.
- Inspect the bounded artifact only; never publish connection strings or data rows.
- Obtain independent review and resolve every actionable thread.
- Require zero-behind current main and merge with expected Head.
- Keep #532 open for Tenant retention/DSAR and exact #533 candidate rerun.

## Decision boundary

- **Clean qualification:** merge the recovery foundation; do not claim final production recovery readiness.
- **Migration/restore/evidence failure:** keep Draft and fix the reproduced cause.
- **Tenant retention/DSAR request:** defer to #546 rather than creating parallel ownership.
