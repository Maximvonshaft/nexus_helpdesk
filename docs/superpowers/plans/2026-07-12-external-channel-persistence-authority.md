# ExternalChannel Unresolved Persistence Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute each task with exact-head verification.

**Goal:** Remove the ExternalChannel unresolved-event monkey-patch chain while preserving hash-idempotent persistence and the existing Bridge public contract.

**Architecture:** Move canonical hash, duplicate lookup, bounded metadata refresh and unique-race recovery into `external_channel_bridge.py`. Remove package-import patching and delete the two obsolete helper modules. Preserve models, tables, migrations and caller-owned transaction boundaries.

**Tech Stack:** Python 3.11, SQLAlchemy ORM, pytest.

## Global Constraints

- Work Item: #654; parent authority: #572.
- Base: `main@528309deed01f246568867e69cdbd235026cfc61`.
- Do not modify models, schemas, Alembic, deployment, Provider or outbound behavior.
- Do not commit/rollback inside persistence authority.
- Retired inbound/replay semantics remain fail closed.

### Task 1: Characterize the broken authority boundary

**Files:**
- Create: `backend/tests/test_external_channel_persistence_authority.py`

- [ ] Assert the Bridge public persistence signature.
- [ ] Assert ORM-native `payload_hash` ownership and canonical hash stability.
- [ ] Assert duplicate reuse, new-row nested transaction and unique-race recovery.
- [ ] Assert no package-import patch and no obsolete modules.
- [ ] Run the focused test and record the expected RED state before implementation.

### Task 2: Establish direct Bridge authority

**Files:**
- Modify: `backend/app/services/external_channel_bridge.py`
- Modify: `backend/app/services/__init__.py`

- [ ] Add canonical payload JSON and SHA-256 helpers.
- [ ] Add active duplicate lookup using source, normalized session and hash.
- [ ] Preserve the public `(db, *, event, source, session_key, error)` contract.
- [ ] Add nested-transaction insert and unique-race winner recovery.
- [ ] Remove the package-import ExternalChannel patch.
- [ ] Run focused tests and Python compilation.

### Task 3: Delete obsolete modules

**Files:**
- Delete: `backend/app/services/external_channel_unresolved_store.py`
- Delete: `backend/app/services/external_channel_payload_hash.py`

- [ ] Remove both files.
- [ ] Search the proposed tree for their module names and patch symbol.
- [ ] Run focused tests and relevant ExternalChannel/backend regressions.

### Task 4: Verify and deliver

**Files:**
- Create: `docs/superpowers/specs/2026-07-12-external-channel-persistence-authority-design.md`
- Create: `docs/superpowers/plans/2026-07-12-external-channel-persistence-authority.md`

- [ ] Compare against current main and require zero unrelated paths.
- [ ] Run all applicable exact-head GitHub checks.
- [ ] Obtain independent review and resolve every actionable thread.
- [ ] Merge with expected Head SHA.
- [ ] Close #654 and update #572 with accepted evidence.
