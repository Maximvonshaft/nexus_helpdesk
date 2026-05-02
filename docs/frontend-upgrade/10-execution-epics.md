# 10 — Execution Epics

## Status

Proposed. These epics are not implementation authorization. They define the recommended execution breakdown after the readiness package is reviewed.

## Execution rule

Implementation must proceed through small, reviewable, rollback-aware epics. Do not combine foundation, design system, Workspace cockpit, WebChat SDK, realtime, and AI Governance into one pull request.

## Epic 1 — Frontend Runtime Foundation

### Objective

Establish the target frontend directory structure and shared runtime foundation without changing visible behavior.

### Scope

- Create `app/`, `features/`, `entities/`, `shared/`, `styles/` structure.
- Move shared API/request infrastructure incrementally.
- Move shared auth helpers incrementally.
- Add domain query-key conventions.
- Add base error boundary and loading/error state patterns.
- Preserve current routes and behavior.

### Non-goals

- No major UI redesign.
- No WebChat SDK rewrite.
- No new backend API.
- No behavior-breaking API client changes.

### Expected files

- `webapp/src/app/**`
- `webapp/src/shared/api/**`
- `webapp/src/shared/auth/**`
- `webapp/src/shared/ui/**`
- `webapp/src/styles/**`

### Acceptance

- `npm run typecheck` passes.
- `npm run lint` passes.
- `npm run build` passes.
- Existing routes still render.
- Login/auth expiration behavior remains intact.

## Epic 2 — Design System Foundation

### Objective

Introduce a governed component and token system for the agentic console.

### Scope

- Tokenize colors, spacing, radii, shadows, status colors.
- Introduce primitive UI components.
- Introduce business UI components.
- Normalize button/card/form/badge patterns.
- Add accessibility requirements to core components.

### Non-goals

- No full page redesign yet.
- No new product workflows.

### Expected files

- `webapp/src/shared/ui/primitives/**`
- `webapp/src/shared/ui/business/**`
- `webapp/src/styles/tokens.css`
- `webapp/src/styles/themes.css`

### Acceptance

- New components are reused in at least one low-risk page.
- No default horizontal overflow.
- Dialog/command interactions are keyboard accessible.
- Status badges do not rely only on color.

## Epic 3 — Workspace Ticket Operations Cockpit

### Objective

Refactor Workspace from a large route module into a modular ticket operations cockpit.

### Scope

- Extract ticket queue.
- Extract queue filters.
- Extract ticket detail header.
- Extract conversation timeline.
- Extract customer context panel.
- Extract evidence panel.
- Extract bulletin panel.
- Extract AI Copilot panel.
- Extract action panel.
- Preserve dirty-state protection.

### Non-goals

- No external outbound dispatch enablement.
- No change to backend workflow semantics unless separately reviewed.

### Expected files

- `webapp/src/features/workspace/**`
- `webapp/src/entities/ticket/**`
- `webapp/src/entities/conversation/**`
- `webapp/src/entities/customer/**`

### Acceptance

- Agent can complete a ticket workflow from one cockpit.
- Refresh does not overwrite unsaved edits.
- AI suggestion is visually distinct from verified facts.
- Safety state is visible before risky reply flows.

## Epic 4 — WebChat Runtime SDK

### Objective

Upgrade the static WebChat widget into a TypeScript-based embeddable runtime while preserving the one-line script contract.

### Scope

- Add `webchat-core` and `webchat-widget` package structure.
- Re-implement current widget behavior in TypeScript.
- Add Shadow DOM isolation.
- Add widget config parser.
- Add theme token model.
- Add compatibility smoke for old snippet.
- Emit `widget.js` for current serving path.

### Non-goals

- No external channel dispatch.
- No public API breaking change.
- No forced customer snippet migration.

### Expected files

- `packages/webchat-core/**`
- `packages/webchat-widget/**`
- `backend/app/static/webchat/widget.js` only through build output or controlled replacement
- `docs/webchat-widget.md` update

### Acceptance

- Existing snippet still works.
- Widget does not depend on host React.
- Widget does not pollute host CSS.
- Mobile viewport works.
- Visitor conversation persists after reload.

## Epic 5 — Realtime Event Runtime

### Objective

Introduce an event runtime that can power live WebChat, ticket, OpenClaw, and runtime status updates.

### Scope

- Define event type model.
- Add frontend event client.
- Add SSE connection abstraction if backend endpoint exists.
- Add fallback polling strategy.
- Add event dedupe.
- Integrate with TanStack Query invalidation/update handlers.

### Non-goals

- Do not remove existing polling until realtime stability is proven.
- Do not expose admin events to public visitors.

### Expected files

- `webapp/src/shared/realtime/**`
- `webapp/src/entities/runtime/**`
- optional backend endpoint only if separately reviewed

### Acceptance

- Events are typed.
- Duplicate events are ignored.
- Disconnection shows reconnecting state.
- Fallback polling works.
- Existing WebChat and Workspace refresh behavior remains safe.

## Epic 6 — AI Governance Studio

### Objective

Upgrade AI Control from JSON-heavy admin page into a governed AI configuration studio.

### Scope

- Split AI Control feature modules.
- Add clearer draft/published states.
- Add schema validation.
- Add business form mode.
- Add JSON mode improvements.
- Add version diff.
- Add sandbox test design/implementation when backend support exists.

### Non-goals

- Do not auto-enable AI replies without policy review.
- Do not expose system prompts to visitors.

### Expected files

- `webapp/src/features/ai-governance/**`
- `webapp/src/entities/ai-config/**`
- `webapp/src/shared/schemas/**`

### Acceptance

- Create/update/publish/rollback still work.
- Invalid draft cannot be published.
- Draft and published states are distinct.
- Sandbox output shows safety state when available.

## Epic 7 — Runtime Control Tower

### Objective

Upgrade runtime/control pages into an operations control tower for health, OpenClaw, jobs, safety, and event activity.

### Scope

- Runtime health cards.
- OpenClaw bridge status.
- Unresolved event visibility.
- Job and queue status.
- Safety gate block visibility.
- Event dock.

### Non-goals

- No destructive replay/drop behavior without explicit confirmation and permission review.

### Expected files

- `webapp/src/features/runtime-control/**`
- `webapp/src/entities/runtime/**`

### Acceptance

- Operator can distinguish healthy/degraded/failing states.
- Last check/sync times are visible.
- Dangerous actions are confirmed.

## Epic 8 — Release Hardening and Documentation

### Objective

Add final quality gates, documentation, and rollback playbooks before production rollout.

### Scope

- E2E smoke scripts.
- WebChat embed smoke.
- Accessibility smoke.
- Bundle size measurement.
- Release checklist.
- Rollback checklist.
- Operator docs.

### Expected files

- `scripts/smoke/**`
- `docs/release/**`
- `docs/frontend-upgrade/**` updates

### Acceptance

- Release checklist completed.
- Rollback plan tested or reviewed.
- Smoke evidence attached to PR.

## PR order

Recommended order:

1. `feature/frontend-runtime-foundation`
2. `feature/agentic-design-system`
3. `feature/ticket-operations-cockpit`
4. `feature/webchat-runtime-sdk`
5. `feature/realtime-event-runtime`
6. `feature/ai-governance-studio`
7. `feature/runtime-control-tower`
8. `feature/frontend-release-hardening`

## Global rules for every implementation PR

Each PR must include:

- summary
- scope
- non-goals
- changed files
- screenshots if UI changed
- test evidence
- risk assessment
- rollback plan
- migration notes

## Stop conditions

Stop implementation and return to review if:

- existing login breaks
- ticket list/detail breaks
- WebChat old snippet breaks
- admin auth behavior changes unintentionally
- public API shape changes without review
- build/typecheck fails
- unsafe AI behavior is introduced
- rollback path is unclear
