# 15 — Release Rollout Plan

## Status

Proposed. This document defines release governance for the frontend upgrade after execution-readiness approval.

## Release principle

Release small, observable, reversible changes. Do not batch unrelated architecture, UI, WebChat, realtime, and AI governance changes into one production release.

## Branch strategy

Planning branch:

```text
planning/frontend-agentic-runtime-readiness
```

Implementation branches:

```text
feature/frontend-runtime-foundation
feature/agentic-design-system
feature/ticket-operations-cockpit
feature/webchat-runtime-sdk
feature/realtime-event-runtime
feature/ai-governance-studio
feature/runtime-control-tower
feature/frontend-release-hardening
```

Each implementation branch should target `main` after the planning package is merged.

## Pull request strategy

Each PR must include:

- scope
- non-goals
- affected files
- screenshots or recordings for UI changes
- test evidence
- risk assessment
- rollback plan
- API compatibility notes
- security notes where applicable

PRs should be small enough to review in one sitting. If a PR requires more than one feature epic to explain, split it.

## Environment strategy

Minimum environments:

1. Local developer environment
2. Preview/staging environment if available
3. Production

If staging is unavailable, the PR must include stronger local smoke evidence and production rollout must be extra conservative.

## CI requirements

Minimum required checks for implementation PRs:

```bash
cd webapp
npm run typecheck
npm run lint
npm run build
```

If backend code or API contracts are touched:

```bash
pytest backend/tests
```

Recommended additional checks:

- Playwright smoke
- WebChat embed smoke
- accessibility smoke
- bundle size measurement

## Release sequence

### Step 1 — Merge planning package

Only docs are merged. No runtime behavior changes.

### Step 2 — Foundation release

Release `feature/frontend-runtime-foundation`.

Criteria:

- no visible behavior changes
- all existing routes still work
- API paths unchanged
- WebChat widget untouched

### Step 3 — Design system release

Release `feature/agentic-design-system`.

Criteria:

- low-risk component adoption
- no major workflow changes
- visual screenshots reviewed

### Step 4 — Workspace cockpit release

Release `feature/ticket-operations-cockpit`.

Criteria:

- Workspace smoke passed
- dirty-state protection verified
- workflow update verified
- AI intake verified

For high-risk layout replacement, use feature flag or staged route switch.

### Step 5 — WebChat runtime release

Release `feature/webchat-runtime-sdk`.

Criteria:

- old snippet compatibility verified
- widget demo smoke passed
- mobile smoke passed
- rollback artifact available

This phase should be rolled out more carefully than admin-console-only changes because external customer websites may embed the widget.

### Step 6 — Realtime runtime release

Release `feature/realtime-event-runtime`.

Criteria:

- fallback polling confirmed
- realtime failure does not block operations
- duplicate events ignored
- stream authenticated

### Step 7 — AI Governance Studio release

Release `feature/ai-governance-studio`.

Criteria:

- create/update/publish/rollback still work
- invalid configs blocked
- draft/published states distinct
- sandbox/diff features gated if backend support incomplete

### Step 8 — Runtime Control Tower and hardening release

Release runtime control and final hardening.

Criteria:

- runtime health visible
- unresolved events visible
- safety gate blocks visible where available
- release checklist complete

## Production smoke checklist

After every production release:

```text
[ ] /healthz returns OK
[ ] /readyz returns ready
[ ] Admin login works
[ ] Dashboard opens
[ ] Workspace opens
[ ] Ticket detail opens
[ ] Workflow update works when affected
[ ] WebChat admin opens
[ ] WebChat demo widget opens when affected
[ ] Visitor message send works when WebChat affected
[ ] Admin WebChat reply works when WebChat affected
[ ] AI Control / Governance opens when affected
[ ] Runtime opens when affected
[ ] Browser console has no critical errors
[ ] No unexpected 401 loops
```

## Observation window

After production release, observe:

- API errors
- frontend console errors
- auth expiration behavior
- WebChat init/send errors
- worker/runtime warnings
- OpenClaw unresolved event volume
- support operator feedback

Minimum observation window:

- docs-only: no runtime observation required
- console-only UI: same business day
- WebChat widget: at least one active business cycle
- realtime or AI governance: at least one active business cycle

## Feature flag guidance

Use feature flags for:

- Workspace full cockpit replacement
- new WebChat SDK rollout
- realtime event stream
- AI sandbox/diff if backend support is partial
- Runtime Control Tower destructive actions

Do not feature-flag simple documentation or low-risk structure-only refactors.

## Release ownership

Every implementation PR should name an owner for:

- technical owner
- product/ops validator
- release executor
- rollback executor

If no owner exists, release should not proceed.

## Release acceptance

A release is accepted only when:

- required checks passed
- targeted smoke passed
- rollback plan is available
- owner confirms post-release health
- no stop condition is triggered

## Stop conditions

Stop rollout and roll back or disable feature if:

- login failure increases
- Workspace cannot load
- WebChat old snippet fails
- public WebChat API errors spike
- safety gate is bypassed
- external dispatch semantics are accidentally changed
- runtime errors prevent operations
- rollback plan cannot be executed
