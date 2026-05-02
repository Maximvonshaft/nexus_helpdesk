# 14 — Migration Plan

## Status

Proposed. This document defines the safe migration order for implementation after the readiness package is approved.

## Migration principle

Use a Strangler Fig migration pattern:

1. Keep current behavior running.
2. Add new structure around existing behavior.
3. Extract low-risk shared layers first.
4. Move feature internals gradually.
5. Replace high-risk surfaces only after smoke coverage exists.
6. Keep rollback paths available.

## Strict order

The migration order is fixed:

```text
1. Frontend runtime foundation
2. Design system foundation
3. Workspace cockpit refactor
4. WebChat runtime SDK
5. Realtime event runtime
6. AI Governance Studio
7. Runtime Control Tower
8. Release hardening
```

Do not start with Workspace redesign, WebChat rewrite, or realtime before the foundation is in place.

## Phase 1 — Frontend Runtime Foundation

### Goal

Create target structure without changing visible behavior.

### Actions

- Create `webapp/src/app/`.
- Create `webapp/src/features/`.
- Create `webapp/src/entities/`.
- Create `webapp/src/shared/`.
- Create `webapp/src/styles/`.
- Move only safe utilities first.
- Add wrappers/exports instead of direct destructive moves where risk exists.
- Preserve existing imports until replacement is verified.

### Must preserve

- login behavior
- router paths
- API base URL behavior
- token handling behavior
- WebChat widget behavior
- page visual output

### Validation

- typecheck
- lint
- build
- manual smoke for login/routes

## Phase 2 — Design System Foundation

### Goal

Create reusable UI primitives and business components.

### Actions

- Add semantic tokens.
- Split global styles into token/base/theme/global layers.
- Add primitives gradually.
- Replace low-risk components first.
- Avoid redesigning feature pages in this phase.

### Must preserve

- page layout stability
- existing form behavior
- current status semantics

### Validation

- no horizontal overflow
- keyboard focus visible
- forms still usable

## Phase 3 — Workspace Cockpit

### Goal

Refactor `workspace.tsx` into feature modules while preserving business behavior.

### Actions

- Extract ticket queue.
- Extract filters.
- Extract selected ticket details.
- Extract conversation timeline.
- Extract evidence panel.
- Extract bulletin panel.
- Extract action form.
- Extract AI insight panel.
- Preserve dirty-state protection.
- Preserve current API calls.

### Migration safety

- Keep old route path `/workspace`.
- Replace internals incrementally.
- Avoid changing backend payload semantics.
- If adding a new cockpit layout, gate it behind a feature flag until verified.

### Validation

- load Workspace
- select ticket
- update workflow
- save AI intake
- confirm dirty-state behavior
- confirm refresh does not overwrite edits

## Phase 4 — WebChat Runtime SDK

### Goal

Upgrade widget implementation without breaking the one-line embed contract.

### Actions

- Add TypeScript package structure.
- Rebuild current widget behavior first.
- Add Shadow DOM isolation after baseline parity.
- Add config parser.
- Add theme tokens.
- Add compatibility smoke.
- Keep output path compatible.

### Migration safety

- Old snippet must keep working.
- Public API shape must not change.
- Keep old widget artifact or rollback copy during rollout.
- Do not require host website React.

### Validation

- demo page loads widget
- launcher opens
- init succeeds
- message sends
- messages load
- reload resumes conversation
- host CSS isolation smoke passes

## Phase 5 — Realtime Event Runtime

### Goal

Add realtime capability without removing stable polling prematurely.

### Actions

- Add event type definitions.
- Add event client abstraction.
- Add SSE support only when backend endpoint is reviewed/available.
- Add fallback polling.
- Add dedupe and reconnect behavior.
- Integrate with TanStack Query.

### Migration safety

- Existing polling remains fallback.
- Realtime failure cannot block ticket/WebChat usage.
- Event stream is admin-authenticated.

### Validation

- connect/reconnect smoke
- fallback polling smoke
- duplicate event ignored
- query invalidation works

## Phase 6 — AI Governance Studio

### Goal

Turn AI Control into operator-friendly governance studio.

### Actions

- Split current page into feature modules.
- Add clearer draft/published visual states.
- Add schema validation.
- Add business form mode.
- Keep JSON mode.
- Add diff and sandbox when backend support is available.

### Migration safety

- Existing create/update/publish/rollback must keep working.
- Invalid configs must not publish.
- Published configs remain separate from draft configs.

### Validation

- create config
- edit draft
- publish
- rollback
- invalid JSON blocked

## Phase 7 — Runtime Control Tower

### Goal

Make operational health and event state visible and actionable.

### Actions

- Consolidate runtime health cards.
- Show OpenClaw status.
- Show unresolved events.
- Show job health.
- Show safety-gate block signals.
- Add event dock when realtime layer exists.

### Migration safety

- Dangerous actions require confirmation.
- Runtime page must handle unauthorized users gracefully.

### Validation

- runtime page loads
- health states render
- warnings render
- replay/drop actions guarded

## Phase 8 — Release Hardening

### Goal

Prepare production rollout.

### Actions

- Add E2E smoke.
- Add WebChat embed smoke.
- Add accessibility smoke.
- Add build-size measurement.
- Update docs.
- Complete release and rollback checklists.

## Branch sequence

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

## Compatibility rules

- Existing routes remain stable.
- Existing API paths remain stable.
- Existing WebChat snippet remains stable.
- Existing admin token behavior remains stable.
- Existing public/admin boundary remains stable.

## Migration stop conditions

Stop and return to review if:

- login breaks
- `/workspace` breaks
- `/webchat` admin breaks
- old WebChat snippet breaks
- `api.ts` split changes auth behavior
- public API shape changes
- safety gate can be bypassed
- rollback path is unclear
