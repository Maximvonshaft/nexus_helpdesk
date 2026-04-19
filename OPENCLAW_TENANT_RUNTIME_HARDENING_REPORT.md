# OpenClaw Tenant Runtime Hardening Report

## Purpose of this PR

This is a **stacked PR** built on top of PR #2 (`feat: add multi-tenant foundation with tenant ai profile and knowledge controls`).

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

### 3. Bridge / event / sync tenant guards
- Updated `backend/app/services/openclaw_bridge.py`
- Added tenant-aware protections in:
  - `ensure_openclaw_conversation_link`
  - `sync_openclaw_conversation`
  - `consume_openclaw_events_once`
- New behavior introduced here:
  - route/account tenant mismatches are now explicitly guarded instead of silently linking
  - successful link creation attaches conversation -> tenant mapping when ticket tenant is known
  - event auto-link now prefers `route/account -> tenant -> ticket` before any wider fallback
  - compatibility fallback is still preserved, but is now explicit and logged as compatibility fallback rather than pretending to be the safe primary path

## What risks were reduced

### Reduced risk: silent cross-tenant conversation linking
Previously, `ensure_openclaw_conversation_link` did not check whether the route/account implied a different tenant than the target ticket.
This PR adds an explicit tenant mismatch guard and logging.

### Reduced risk: unsafe event auto-link as primary behavior
Previously, unknown sessions could fall back too quickly toward global recipient-based recovery.
This PR changes the order so that tenant-safe route resolution is attempted first.
Only when tenant context cannot be derived does the system enter an explicitly logged compatibility fallback branch.

### Reduced risk: hidden runtime ambiguity in logs
This PR adds or strengthens explicit log semantics around:
- `openclaw_bridge_tenant_mismatch`
- `openclaw_event_unresolved_tenant_context`
- `openclaw_event_compat_fallback`
- `openclaw_conversation_sync_tenant_guard_failed`
- `openclaw_conversation_tenant_attached`

## Why shared cursor is still preserved

The event cursor remains operationally shared in this PR.
That is intentional.

Reason:
- the existing runtime chain is already built around a shared event consumption model
- per-tenant cursoring would be a larger behavior change and is not proven safe within this stacked PR scope
- the immediate risk reduction comes from making event-to-tenant attribution stricter, not from changing the cursor model itself

Shared cursor is acceptable **only under these assumptions**:
- session -> conversation link remains unique enough for resolution
- route/account -> tenant derivation is enforced before wider fallback
- compatibility fallback is explicit and auditable

## What this PR does not claim to fully finish

This PR **does not** claim complete end-to-end tenant hardening of every OpenClaw runtime path.

Still incomplete / partially hardened:

### Event / conversation compatibility fallback still exists
The historical recovery logic is no longer pretending to be the safe primary path, but it still exists for compatibility.
That means some residual risk remains when route/account tenant context is unavailable.

### Shared cursor remains a design tradeoff
It is now documented and tolerated, but not eliminated.
If future behavior shows bleed risk, a deeper redesign may be required.

### Bridge-first + MCP fallback model remains
This PR does not remove fallback. It keeps the existing bridge-first / fallback architecture intact and only makes the primary path more tenant-aware and the fallback path more explicit.

## Remaining risks

1. **Compatibility fallback still exists**
   If route/account tenant context cannot be derived, the system may still use an explicitly logged compatibility fallback branch.

2. **Shared cursor operational model**
   Event cursoring is still shared. This remains acceptable only if session and route mapping continue to be reliable enough in practice.

3. **Not every runtime path is fully end-to-end tenant-safe**
   This PR should be described as runtime hardening in progress, not final completion.

## Merge order recommendation

1. Merge **PR #2** first
2. Then merge this stacked PR

This PR assumes the tenant foundation from PR #2 is already present.

## Honest current status

After this PR:
- tenant -> OpenClaw projection state is more formally grounded
- outbound message routing is more tenant-aware than before
- bridge / event / sync paths now have stronger tenant guards than before
- but the system should still be described as **runtime hardening in progress**, not as a fully finished multi-tenant OpenClaw execution model
