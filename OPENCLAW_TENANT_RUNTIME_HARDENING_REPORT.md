# OpenClaw Tenant Runtime Hardening Report

## Purpose of this PR

This is a **stacked PR** built on top of PR #2 (`feat/multi-tenant foundation ...`).

PR #2 establishes the multi-tenant control plane and already includes some projection-oriented groundwork.
This PR does **not** redefine that scope. Instead, it focuses on the next layer:

- tenant-aware OpenClaw runtime projection hardening
- tenant-aware outbound message routing hardening
- conversation / event runtime boundary tightening

## Why this is a stacked PR

PR #2 should remain the review unit for:
- tenant data foundation
- tenant memberships
- tenant AI profile
- tenant knowledge base
- tenant-aware ticket / lite control plane

This PR exists because the next problem is different:
- runtime execution hardening
- message route correctness
- OpenClaw execution boundary tightening

Keeping this as a stacked PR reduces review confusion and makes merge / rollback safer.

## What was already present on top of PR #2 before this PR started

The following projection/runtime-adjacent pieces were already present in the PR #2 branch head and are therefore treated here as inherited baseline rather than newly introduced by this PR:

- `backend/app/openclaw_projection_models.py`
- `backend/app/openclaw_projection_schemas.py`
- `backend/app/services/tenant_openclaw_projection_service.py`
- projection registration in `backend/app/main.py`
- projection refresh hooks in `backend/app/api/tenants.py`
- tenant persona / knowledge injection already connected into `background_jobs.py`

This PR continues from that state rather than pretending the branch was perfectly clean.

## What this PR actually adds

### 1. Formal migration for tenant OpenClaw projection state
- Added `backend/alembic/versions/20260419_0013_openclaw_projection.py`
- This makes `tenant_openclaw_agents` no longer just an ORM / service concept in code, but a real schema migration unit

### 2. Tenant-aware outbound routing hardening
- Updated `backend/app/services/message_dispatch.py`
- Outbound dispatch now:
  - resolves the ticket tenant
  - resolves tenant projection route context
  - prefers projection default account/channel when missing on the conversation link
  - checks tenant/account mismatches before sending
  - blocks mismatched routes instead of silently continuing
  - adds tenant / projection metadata into audit logging

## What this PR does not claim to fully finish

This PR **does not** claim complete end-to-end tenant hardening of every OpenClaw runtime path.

In particular, the following remain incomplete or only partially hardened:

### `openclaw_bridge.py`
Still needs deeper tenant-aware tightening in:
- `ensure_openclaw_conversation_link`
- `sync_openclaw_conversation`
- `consume_openclaw_events_once`

### Event / conversation auto-link behavior
The historical compatibility logic around resolving unknown sessions by recipient/contact still needs stricter tenant-aware handling. This PR does not claim that all such legacy compatibility branches are fully eliminated yet.

### Inbound event isolation
Cursoring remains operationally shared. This is not automatically wrong, but further work is needed to guarantee event-to-tenant attribution remains fully explicit and auditable under all fallback paths.

## Remaining risks

1. **Conversation auto-link risk**
   Legacy session-to-ticket recovery paths may still need additional tenant guards.

2. **Shared cursor operational model**
   Global event cursoring is still a design point to watch. It needs explicit explanation and possibly further narrowing if future behavior shows tenant bleed risk.

3. **Bridge-side tenant enforcement not fully complete**
   The bridge and conversation sync code still needs another pass so that route/account/session resolution is consistently tenant-aware, not just the outbound dispatch layer.

## Merge order recommendation

1. Merge **PR #2** first
2. Then merge this stacked PR

This PR assumes the tenant foundation from PR #2 is already present.

## Honest current status

After this PR:
- tenant -> OpenClaw projection state is more formally grounded
- outbound message routing is more tenant-aware than before
- but the system should still be described as **runtime hardening in progress**, not as a fully finished multi-tenant OpenClaw execution model
