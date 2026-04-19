# OpenClaw Unresolved Tenant Quarantine Report

## Why this PR exists

This is a stacked PR on top of PR #3.

PR #2 established the multi-tenant control plane.
PR #3 started tenant-aware runtime hardening.
The remaining highest-risk area was unresolved tenant context during OpenClaw event/session ingestion.

That problem is different from general runtime hardening:
- the system may receive a session/event before tenant attribution is stable
- if the system guesses too aggressively, low-probability cross-tenant contamination can occur

This PR exists to move that class of input from implicit guessing to explicit quarantine.

## What this PR does

### 1. Adds a formal unresolved-event quarantine model
- Added `backend/app/openclaw_quarantine_models.py`
- Introduces `OpenClawUnresolvedEvent`
- This is not just a log artifact; it is the formal holding area for unresolved OpenClaw runtime inputs

### 2. Adds a minimal quarantine/replay service
- Added `backend/app/services/openclaw_quarantine_service.py`
- Provides:
  - `quarantine_openclaw_event(...)`
  - `mark_quarantine_event_dropped(...)`
  - `replay_quarantined_event(...)`

Replay is intentionally minimal:
- it attempts safe re-link only after route/account/session context is available enough
- it does not pretend to be a full admin workflow yet

### 3. Changes event consume behavior
- Updated `backend/app/services/openclaw_bridge.py`
- `consume_openclaw_events_once` now prefers:
  - session -> conversation link
  - route/account -> tenant
  - tenant -> ticket
- If that chain cannot be completed safely:
  - the event is quarantined
  - it is not silently routed via broad global guesswork

## What this PR does not do

- It does not redesign shared cursor into per-tenant cursoring
- It does not add full admin UI for unresolved event review
- It does not remove bridge-first + fallback architecture
- It does not claim every runtime path is now perfectly end-to-end tenant-safe

## Why shared cursor is still preserved

Shared cursor remains in place in this PR.

Reason:
- the existing runtime/event consumption architecture is already centered around a shared cursor model
- changing that here would be a larger architectural change than this quarantine PR is meant to carry
- the highest immediate risk was not the cursor itself, but the unsafe behavior that could follow when tenant context was unresolved

So the security improvement in this PR is:
- unresolved events are quarantined instead of guessed
- not a cursor redesign

## Remaining risks

1. Shared cursor still exists
   - acceptable for now only because unresolved events are no longer silently broad-matched by default
   - but still a future design pressure point

2. Replay is minimal
   - service-level replay exists
   - but a richer operator/admin review flow is still future work

3. Not every path is fully tenant-complete
   - this PR reduces one specific high-risk class
   - it should not be marketed as final completion of all multi-tenant runtime safety work

## Honest status after this PR

After this PR:
- unresolved tenant context no longer defaults to broad automatic ticket guessing in the main event-consume path
- quarantine is now a real first-class concept
- minimal replay exists
- the system is safer than before, but still not the final endpoint of tenant-safe runtime design
