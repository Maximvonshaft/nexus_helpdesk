# 18 — Frontend Execution Construction Blueprint

## Status

Planning / execution blueprint. This document is based on the current `main` branch after the frontend readiness, runtime foundation, design system foundation, and Radix dependency work have been merged.

This document does not authorize a large rewrite. It converts the approved frontend direction into minimal, reviewable construction units.

## Current `main` baseline

Current `main` already contains:

- execution readiness package under `docs/frontend-upgrade/`
- frontend layer skeleton under `webapp/src/app`, `features`, `entities`, `shared`, `styles`
- Frontend CI workflow
- design-system foundation:
  - `Button`
  - `Badge`
  - `Card`, `CardHeader`, `CardBody`
  - `StatusBadge`
  - `SafetyGateBadge`
  - inactive `tokens.css`
  - inactive `components.css`
- Radix dependency layer:
  - `@radix-ui/react-dialog`
  - `@radix-ui/react-dropdown-menu`
  - `@radix-ui/react-popover`
  - `@radix-ui/react-select`
  - `@radix-ui/react-tabs`
  - `@radix-ui/react-tooltip`

Current `main` does **not** yet contain:

- Radix wrapper components
- active design-system CSS imports
- real page adoption of new shared UI
- Runtime Control Tower rewrite
- AI Governance Studio rewrite
- WebChat Control Center rewrite
- WebChat SDK / Shadow DOM runtime
- Workspace Ticket Operations Cockpit rewrite

## Global execution law

Every construction unit must follow these rules:

1. Create a fresh branch from latest `main`.
2. Keep one PR to one construction unit.
3. Do not mix foundation, component wrappers, page migration, backend API changes, and WebChat widget changes in the same PR.
4. Preserve existing route paths unless the PR explicitly says otherwise.
5. Preserve existing API contracts unless the API contract map is updated and reviewed.
6. Preserve WebChat public widget compatibility.
7. Preserve admin auth/session behavior.
8. Run Frontend CI for frontend changes.
9. Include rollback plan in every PR.
10. Do not merge if latest head has no green CI.

## Global required checks

Frontend-only PRs:

```bash
cd webapp
npm ci
npm run typecheck
npm run lint
npm run build
```

Backend touched:

```bash
cd backend
python3 -m compileall app scripts
pytest -q
alembic heads
alembic upgrade head
```

Release/smoke checks when affected:

```text
[ ] login smoke
[ ] dashboard smoke
[ ] Workspace smoke
[ ] WebChat admin smoke
[ ] WebChat widget smoke
[ ] AI Control / Governance smoke
[ ] Runtime smoke
```

---

# Phase 1 — Radix Wrapper Primitives

## Goal

Convert raw Radix dependencies into NexusDesk-owned wrapper components under `shared/ui/primitives`.

Feature code must import NexusDesk wrappers, not raw Radix packages.

## Construction unit 1.1 — Dialog wrapper

### Branch

```text
feature/radix-dialog-wrapper
```

### Commit

```text
feat: add Dialog primitive wrapper
```

### Files to add

```text
webapp/src/shared/ui/primitives/Dialog.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/shared/ui/index.ts
webapp/src/styles/components.css
webapp/src/shared/ui/DESIGN_SYSTEM_FOUNDATION.md
```

### Implementation details

- Wrap `@radix-ui/react-dialog`.
- Export:
  - `DialogRoot`
  - `DialogTrigger`
  - `DialogPortal`
  - `DialogOverlay`
  - `DialogContent`
  - `DialogTitle`
  - `DialogDescription`
  - `DialogClose`
- Use `clsx` and `nd-dialog*` class names.
- Do not add page usage in this PR.
- Do not import `components.css` into app yet unless this PR explicitly chooses to activate only safe base classes. Recommended: keep CSS inactive until first adoption PR.

### Non-goals

- No Workspace usage.
- No WebChat usage.
- No AI Control usage.
- No route import changes.
- No backend changes.

### Acceptance

```text
[ ] npm ci passes
[ ] npm run typecheck passes
[ ] npm run lint passes
[ ] npm run build passes
[ ] no existing page imports changed
```

### Rollback

Revert PR. No active route should depend on it.

## Construction unit 1.2 — Tooltip wrapper

### Branch

```text
feature/radix-tooltip-wrapper
```

### Commit

```text
feat: add Tooltip primitive wrapper
```

### Files to add

```text
webapp/src/shared/ui/primitives/Tooltip.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/styles/components.css
```

### Implementation details

- Wrap `@radix-ui/react-tooltip`.
- Export:
  - `TooltipProvider`
  - `TooltipRoot`
  - `TooltipTrigger`
  - `TooltipContent`
- Use `nd-tooltip*` class names.
- Default delay should be conservative and not disruptive.

### Non-goals

- No page adoption yet.

### Acceptance

Same as unit 1.1.

## Construction unit 1.3 — Popover wrapper

### Branch

```text
feature/radix-popover-wrapper
```

### Commit

```text
feat: add Popover primitive wrapper
```

### Files to add

```text
webapp/src/shared/ui/primitives/Popover.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/styles/components.css
```

### Implementation details

- Wrap `@radix-ui/react-popover`.
- Export:
  - `PopoverRoot`
  - `PopoverTrigger`
  - `PopoverPortal`
  - `PopoverContent`
  - `PopoverClose`
- Use `nd-popover*` class names.

### Acceptance

Same as unit 1.1.

## Construction unit 1.4 — DropdownMenu wrapper

### Branch

```text
feature/radix-dropdown-wrapper
```

### Commit

```text
feat: add DropdownMenu primitive wrapper
```

### Files to add

```text
webapp/src/shared/ui/primitives/DropdownMenu.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/styles/components.css
```

### Implementation details

- Wrap `@radix-ui/react-dropdown-menu`.
- Export:
  - `DropdownMenuRoot`
  - `DropdownMenuTrigger`
  - `DropdownMenuContent`
  - `DropdownMenuItem`
  - `DropdownMenuSeparator`
  - `DropdownMenuLabel`
- Use `nd-dropdown*` class names.

### Acceptance

Same as unit 1.1.

## Construction unit 1.5 — Tabs wrapper

### Branch

```text
feature/radix-tabs-wrapper
```

### Commit

```text
feat: add Tabs primitive wrapper
```

### Files to add

```text
webapp/src/shared/ui/primitives/Tabs.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/styles/components.css
```

### Implementation details

- Wrap `@radix-ui/react-tabs`.
- Export:
  - `TabsRoot`
  - `TabsList`
  - `TabsTrigger`
  - `TabsContent`
- Use `nd-tabs*` class names.

### Acceptance

Same as unit 1.1.

## Construction unit 1.6 — Select wrapper

### Branch

```text
feature/radix-select-wrapper
```

### Commit

```text
feat: add Select primitive wrapper
```

### Files to add

```text
webapp/src/shared/ui/primitives/Select.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/styles/components.css
```

### Implementation details

- Wrap `@radix-ui/react-select`.
- Export:
  - `SelectRoot`
  - `SelectTrigger`
  - `SelectValue`
  - `SelectContent`
  - `SelectItem`
  - `SelectLabel`
  - `SelectSeparator`
- Use `nd-select*` class names.

### Acceptance

Same as unit 1.1.

## Construction unit 1.7 — Optional AlertDialog dependency and wrapper

### Important current-main fact

Current `main` does **not** include `@radix-ui/react-alert-dialog`.

### Branch

```text
feature/radix-alert-dialog-wrapper
```

### Commit sequence

```text
chore: add AlertDialog Radix dependency
feat: add AlertDialog primitive wrapper
```

### Required dependency command

Run inside `webapp/`:

```bash
npm install @radix-ui/react-alert-dialog
```

Commit both:

```text
webapp/package.json
webapp/package-lock.json
```

### Files to add

```text
webapp/src/shared/ui/primitives/AlertDialog.tsx
```

### Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/styles/components.css
```

### Acceptance

```text
[ ] package-lock generated by npm, not hand-edited
[ ] npm ci passes
[ ] typecheck/lint/build pass
```

### Non-goal

Do not use AlertDialog in business pages until wrapper CI is green.

---

# Phase 2 — Design System CSS Activation

## Goal

Activate NexusDesk token/component CSS safely after wrappers exist.

## Construction unit 2.1 — CSS composition entry

### Branch

```text
feature/design-system-css-composition
```

### Commit

```text
feat: compose design system styles
```

### Files to add

```text
webapp/src/styles/design-system.css
```

### Files to update

```text
webapp/src/styles.css
```

### Implementation details

- Add `design-system.css` that imports:
  - `./styles/tokens.css`
  - `./styles/components.css`
- Import `design-system.css` from `styles.css` at a controlled position.
- Confirm no existing class names collide destructively.

### Non-goals

- No page migration.
- No component adoption.
- No visual redesign.

### Acceptance

```text
[ ] build passes
[ ] login visual smoke has no breakage
[ ] dashboard visual smoke has no breakage
[ ] no horizontal overflow introduced
```

### Rollback

Revert CSS import PR.

---

# Phase 3 — Low-risk Runtime Page Adoption

## Goal

Use new design-system components on a non-critical status surface before touching Workspace or WebChat.

Target page: `/runtime`.

## Construction unit 3.1 — Extract runtime feature shell

### Branch

```text
feature/runtime-feature-shell
```

### Commit

```text
refactor: extract runtime feature shell
```

### Files to add

```text
webapp/src/features/runtime-control/RuntimeControlRoute.tsx
webapp/src/features/runtime-control/components/RuntimePageHeader.tsx
webapp/src/features/runtime-control/components/RuntimeSection.tsx
webapp/src/features/runtime-control/index.ts
```

### Files to update

```text
webapp/src/routes/runtime.tsx
```

### Implementation details

- Move rendering orchestration only.
- Keep API calls and behavior unchanged.
- Route path remains `/runtime`.
- Do not redesign yet.

### Acceptance

```text
[ ] /runtime renders same data
[ ] typecheck/lint/build pass
[ ] no API changes
```

## Construction unit 3.2 — Runtime status cards with Card/StatusBadge

### Branch

```text
feature/runtime-status-cards-design-system
```

### Commit

```text
feat: apply design system to runtime status cards
```

### Files to add/update

```text
webapp/src/features/runtime-control/components/RuntimeHealthCard.tsx
webapp/src/features/runtime-control/components/OpenClawHealthCard.tsx
webapp/src/features/runtime-control/components/JobHealthCard.tsx
webapp/src/features/runtime-control/components/ReadinessCard.tsx
webapp/src/features/runtime-control/RuntimeControlRoute.tsx
```

### Components to use

```text
Card
CardHeader
CardBody
StatusBadge
Tooltip
```

### Non-goals

- No dangerous runtime actions.
- No replay/drop behavior change.
- No backend API change.

### Acceptance

```text
[ ] Runtime page smoke passes
[ ] status labels remain semantically correct
[ ] healthy/degraded/failing states are visually distinct
[ ] typecheck/lint/build pass
```

---

# Phase 4 — Runtime Control Tower

## Goal

Turn `/runtime` into a production-grade control tower without modifying backend semantics.

## Construction unit 4.1 — Runtime data model normalization

### Branch

```text
feature/runtime-entity-models
```

### Commit

```text
refactor: add runtime entity models
```

### Files to add

```text
webapp/src/entities/runtime/types.ts
webapp/src/entities/runtime/queryKeys.ts
webapp/src/entities/runtime/index.ts
```

### Files to update

```text
webapp/src/lib/api.ts  # only if adding typed wrapper exports without behavior change
```

### Acceptance

```text
[ ] no API path changes
[ ] typecheck/lint/build pass
```

## Construction unit 4.2 — Runtime event/status panels

### Branch

```text
feature/runtime-control-panels
```

### Commit

```text
feat: add runtime control panels
```

### Files to add

```text
webapp/src/features/runtime-control/components/ProductionReadinessPanel.tsx
webapp/src/features/runtime-control/components/OpenClawBridgePanel.tsx
webapp/src/features/runtime-control/components/JobsPanel.tsx
webapp/src/features/runtime-control/components/SafetyGatePanel.tsx
```

### Components to use

```text
Tabs
Card
StatusBadge
SafetyGateBadge
Tooltip
```

### Non-goals

- No destructive actions unless confirmation flow exists.
- No backend mutation changes.

---

# Phase 5 — AI Governance Studio Foundation

## Goal

Refactor AI Control into `features/ai-governance` without changing backend API behavior.

## Construction unit 5.1 — Extract AI Governance feature shell

### Branch

```text
feature/ai-governance-feature-shell
```

### Commit

```text
refactor: extract AI governance feature shell
```

### Files to add

```text
webapp/src/features/ai-governance/AiGovernanceRoute.tsx
webapp/src/features/ai-governance/components/GovernanceHeader.tsx
webapp/src/features/ai-governance/components/ConfigResourceList.tsx
webapp/src/features/ai-governance/components/ConfigEditorPanel.tsx
webapp/src/features/ai-governance/index.ts
```

### Files to update

```text
webapp/src/routes/ai-control.tsx
```

### Implementation details

- Move rendering structure only.
- Preserve existing API calls, publish, rollback, and draft editing behavior.
- Route remains `/ai-control`.

### Acceptance

```text
[ ] AI config list loads
[ ] draft save works
[ ] publish works
[ ] rollback works
[ ] typecheck/lint/build pass
```

## Construction unit 5.2 — Governance tabs and draft/published cards

### Branch

```text
feature/ai-governance-design-system
```

### Commit

```text
feat: apply design system to AI governance states
```

### Files to add/update

```text
webapp/src/features/ai-governance/components/ConfigTypeTabs.tsx
webapp/src/features/ai-governance/components/DraftPublishedStateCard.tsx
webapp/src/features/ai-governance/components/VersionHistoryPanel.tsx
webapp/src/features/ai-governance/components/PolicyGuardrailSummary.tsx
```

### Components to use

```text
Tabs
Card
Badge
StatusBadge
SafetyGateBadge
Tooltip
```

### Non-goals

- No schema change.
- No auto-reply enablement.
- No backend behavior change.

---

# Phase 6 — WebChat Control Center Foundation

## Goal

Refactor WebChat admin UI into feature modules without rewriting the public widget.

## Construction unit 6.1 — Extract WebChat admin feature shell

### Branch

```text
feature/webchat-admin-feature-shell
```

### Commit

```text
refactor: extract WebChat admin feature shell
```

### Files to add

```text
webapp/src/features/webchat-admin/WebChatAdminRoute.tsx
webapp/src/features/webchat-admin/components/WebChatInbox.tsx
webapp/src/features/webchat-admin/components/WebChatThreadPanel.tsx
webapp/src/features/webchat-admin/components/WebChatReplyComposer.tsx
webapp/src/features/webchat-admin/components/WebChatSnippetCard.tsx
webapp/src/features/webchat-admin/index.ts
```

### Files to update

```text
webapp/src/routes/webchat.tsx
```

### Implementation details

- Preserve polling behavior.
- Preserve reply API behavior.
- Preserve safety-gate flags.
- Preserve snippet behavior.

### Non-goals

- No widget rewrite.
- No public API changes.
- No Shadow DOM.

### Acceptance

```text
[ ] WebChat admin conversation list loads
[ ] thread loads
[ ] reply still works
[ ] snippet display still works
[ ] typecheck/lint/build pass
```

## Construction unit 6.2 — WebChat admin design-system adoption

### Branch

```text
feature/webchat-admin-design-system
```

### Commit

```text
feat: apply design system to WebChat admin
```

### Components to use

```text
Card
Badge
StatusBadge
SafetyGateBadge
Tooltip
Popover
Tabs
```

### Non-goals

- Do not change public visitor widget.
- Do not change API contract.

---

# Phase 7 — WebChat Widget SDK Runtime

## Goal

Convert `backend/app/static/webchat/widget.js` into a proper SDK build while preserving current one-line embed compatibility.

## Construction unit 7.1 — Widget SDK architecture docs and build plan

### Branch

```text
feature/webchat-widget-sdk-plan
```

### Commit

```text
docs: add WebChat widget SDK implementation plan
```

### Files to add

```text
docs/frontend-upgrade/webchat-widget-sdk-implementation-plan.md
```

### Acceptance

Docs-only, CI green.

## Construction unit 7.2 — Add widget package skeleton

### Branch

```text
feature/webchat-widget-package-skeleton
```

### Commit

```text
feat: add WebChat widget package skeleton
```

### Files to add

```text
packages/webchat-core/README.md
packages/webchat-core/src/index.ts
packages/webchat-widget/README.md
packages/webchat-widget/src/index.ts
packages/webchat-widget/src/mount.ts
packages/webchat-widget/src/config.ts
packages/webchat-widget/src/transport.ts
```

### Non-goals

- Do not replace active `backend/app/static/webchat/widget.js` yet.
- Do not change public API.

## Construction unit 7.3 — Build generated widget artifact in parallel path

### Branch

```text
feature/webchat-widget-sdk-parallel-build
```

### Files to add/update

```text
packages/webchat-widget/vite.config.ts
webapp/package.json or root scripts if needed
backend/app/static/webchat/widget.next.js
```

### Implementation details

- Build `widget.next.js` side-by-side.
- Keep current `widget.js` active.
- Add smoke page for `widget.next.js` only.

### Acceptance

```text
[ ] old widget.js still untouched
[ ] widget.next.js builds
[ ] visitor-only smoke passes against next widget if test harness exists
```

## Construction unit 7.4 — Shadow DOM runtime

### Branch

```text
feature/webchat-widget-shadow-dom-runtime
```

### Implementation details

- Mount into Shadow DOM.
- Isolate CSS.
- Preserve script attribute config parser.
- Preserve conversation id and visitor token flow.

### Non-goals

- Do not point production snippet to new SDK yet.

## Construction unit 7.5 — Controlled switch from old widget.js to SDK widget.js

### Branch

```text
feature/webchat-widget-sdk-cutover
```

### Preconditions

```text
[ ] old snippet compatibility smoke passes
[ ] visitor init/send/poll passes
[ ] mobile viewport smoke passes
[ ] rollback artifact available
```

### Rollback

Restore previous `backend/app/static/webchat/widget.js` artifact.

---

# Phase 8 — Workspace Ticket Operations Cockpit

## Goal

Refactor the highest-risk page last, after shared UI, Radix wrappers, Runtime, AI Governance, and WebChat admin have proven the pattern.

## Construction unit 8.1 — Workspace feature shell extraction

### Branch

```text
feature/workspace-feature-shell
```

### Commit

```text
refactor: extract workspace feature shell
```

### Files to add

```text
webapp/src/features/workspace/WorkspaceRoute.tsx
webapp/src/features/workspace/components/TicketQueue.tsx
webapp/src/features/workspace/components/TicketQueueFilters.tsx
webapp/src/features/workspace/components/TicketDetailHeader.tsx
webapp/src/features/workspace/components/ConversationTimeline.tsx
webapp/src/features/workspace/components/EvidencePanel.tsx
webapp/src/features/workspace/components/BulletinPanel.tsx
webapp/src/features/workspace/components/AiCopilotPanel.tsx
webapp/src/features/workspace/components/TicketActionPanel.tsx
webapp/src/features/workspace/index.ts
```

### Files to update

```text
webapp/src/routes/workspace.tsx
```

### Implementation details

- Extract without changing behavior.
- Preserve dirty-state protection.
- Preserve current polling/refresh behavior.
- Preserve all API calls.

### Acceptance

```text
[ ] ticket list loads
[ ] filter/search works
[ ] ticket selection works
[ ] ticket detail loads
[ ] workflow update works
[ ] AI intake save works
[ ] dirty-state protection works
[ ] typecheck/lint/build pass
```

## Construction unit 8.2 — Conversation timeline component adoption

### Branch

```text
feature/workspace-conversation-timeline
```

### Goal

Make message display reusable and safe.

### Acceptance

```text
[ ] no HTML injection
[ ] attachments still display
[ ] OpenClaw transcript still displays
```

## Construction unit 8.3 — Evidence and Safety Gate panels

### Branch

```text
feature/workspace-evidence-safety-panels
```

### Components to use

```text
Card
SafetyGateBadge
StatusBadge
Tooltip
Popover
```

### Non-goals

- Do not change backend safety decisions.
- Do not enable external outbound.

## Construction unit 8.4 — Cockpit layout

### Branch

```text
feature/workspace-cockpit-layout
```

### Preconditions

All previous Workspace extraction units pass CI and smoke.

### Goal

Three-panel layout:

```text
Queue | Ticket + Conversation | AI + Actions
```

### Acceptance

```text
[ ] no horizontal overflow
[ ] usable at tablet width
[ ] dirty state preserved
[ ] no API behavior changes
```

---

# Phase 9 — Realtime Event Runtime

## Goal

Add realtime event support without removing existing polling until proven stable.

## Construction unit 9.1 — Frontend realtime client skeleton

### Branch

```text
feature/realtime-client-foundation
```

### Files to add

```text
webapp/src/shared/realtime/eventTypes.ts
webapp/src/shared/realtime/eventClient.ts
webapp/src/shared/realtime/useEventStream.ts
webapp/src/shared/realtime/index.ts
```

### Non-goals

- No backend endpoint required yet.
- No page usage yet.

## Construction unit 9.2 — SSE backend endpoint proposal

### Branch

```text
feature/realtime-api-stream
```

### Files likely touched

```text
backend/app/api/events.py
backend/app/main.py
backend/app/schemas.py
backend/tests/test_realtime_events.py
```

### Non-goals

- Do not remove polling.
- Do not expose public visitor events.

## Construction unit 9.3 — Runtime event dock adoption

### Branch

```text
feature/runtime-event-dock
```

### Acceptance

```text
[ ] SSE works when available
[ ] fallback polling remains available
[ ] duplicate events ignored
[ ] unauthorized stream is rejected
```

---

# Phase 10 — Branch Cleanup / Governance

## Goal

Reduce confusion from historical branches after the main line stabilizes.

## Construction unit 10.1 — Branch cleanup report

### Branch

```text
planning/branch-cleanup-report
```

### File to add

```text
docs/repo-governance/branch-cleanup-report.md
```

### Content

Classify branches as:

- keep active
- merged/archive
- superseded/close
- requires rebase audit

### Non-goal

Do not delete remote branches automatically in the same PR.

## Construction unit 10.2 — Optional remote branch deletion

Only after human approval.

---

# Full execution order

Execute in this order:

```text
1. feature/radix-dialog-wrapper
2. feature/radix-tooltip-wrapper
3. feature/radix-popover-wrapper
4. feature/radix-dropdown-wrapper
5. feature/radix-tabs-wrapper
6. feature/radix-select-wrapper
7. optional feature/radix-alert-dialog-wrapper
8. feature/design-system-css-composition
9. feature/runtime-feature-shell
10. feature/runtime-status-cards-design-system
11. feature/runtime-entity-models
12. feature/runtime-control-panels
13. feature/ai-governance-feature-shell
14. feature/ai-governance-design-system
15. feature/webchat-admin-feature-shell
16. feature/webchat-admin-design-system
17. feature/webchat-widget-sdk-plan
18. feature/webchat-widget-package-skeleton
19. feature/webchat-widget-sdk-parallel-build
20. feature/webchat-widget-shadow-dom-runtime
21. feature/webchat-widget-sdk-cutover
22. feature/workspace-feature-shell
23. feature/workspace-conversation-timeline
24. feature/workspace-evidence-safety-panels
25. feature/workspace-cockpit-layout
26. feature/realtime-client-foundation
27. feature/realtime-api-stream
28. feature/runtime-event-dock
29. planning/branch-cleanup-report
```

## Hard stop rules

Stop and return to review if any of these happen:

```text
[ ] login breaks
[ ] Workspace ticket list/detail breaks
[ ] WebChat old snippet breaks
[ ] admin auth behavior changes unintentionally
[ ] public API shape changes without API contract update
[ ] package-lock is hand-edited
[ ] latest PR head has no green CI
[ ] safety gate can be bypassed
[ ] external outbound semantics are changed accidentally
[ ] rollback path is unclear
```

## Final target state

When the construction plan is complete, NexusDesk frontend should have:

```text
- NexusDesk-owned Radix wrappers
- activated semantic design tokens
- reusable business status/safety components
- Runtime Control Tower
- AI Governance Studio
- WebChat Control Center
- SDK-based WebChat widget runtime
- Workspace Ticket Operations Cockpit
- realtime event runtime with fallback polling
- clean branch governance
```

This is the professional path from the current `main` branch to an agent-native customer operations console.
