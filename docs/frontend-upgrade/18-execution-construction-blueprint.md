# 18 — Frontend Fast-Track Execution Construction Blueprint

## Status

Fast-track execution blueprint. This document is based on the current `main` branch after the frontend readiness, runtime foundation, design system foundation, Radix adoption runbook, and Radix dependency work have been merged.

This version intentionally replaces the overly granular 29-PR plan with a faster 6-PR execution plan. The goal is speed with controlled blast radius, not academic decomposition.

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

## Fast-track principle

We will not split one component into one PR. That is safe but too slow.

The new rule is:

```text
One PR = one complete visible layer or one complete product surface.
```

This gives speed while keeping rollback possible.

## Non-negotiable guardrails

Fast does not mean reckless. Every PR must still follow these gates:

1. Branch from latest `main`.
2. One PR must have one clear rollback path.
3. No hand-edited lockfile.
4. Latest head must have green CI.
5. Existing route paths remain stable.
6. Existing API paths remain stable unless explicitly reviewed.
7. Existing WebChat public widget contract remains stable.
8. Admin auth/session behavior must not change unintentionally.
9. External outbound dispatch semantics must not change accidentally.
10. Workspace/WebChat core changes must include targeted smoke notes.

## Required checks

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

Smoke when affected:

```text
[ ] login
[ ] dashboard
[ ] runtime
[ ] AI Control / Governance
[ ] WebChat admin
[ ] WebChat visitor widget
[ ] Workspace ticket list/detail/update
```

---

# Fast-Track PR 1 — Radix Wrappers + Design System CSS Activation

## Branch

```text
feature/design-system-radix-wrapper-foundation
```

## Commit target

```text
feat: add Radix-backed design system primitives
```

## Goal

Convert the existing Radix dependencies into NexusDesk-owned wrappers and activate NexusDesk design-system CSS in one controlled PR.

This PR gives us the real UI foundation needed for fast page adoption.

## Files to add

```text
webapp/src/shared/ui/primitives/Dialog.tsx
webapp/src/shared/ui/primitives/Tooltip.tsx
webapp/src/shared/ui/primitives/Popover.tsx
webapp/src/shared/ui/primitives/DropdownMenu.tsx
webapp/src/shared/ui/primitives/Tabs.tsx
webapp/src/shared/ui/primitives/Select.tsx
webapp/src/styles/design-system.css
```

## Files to update

```text
webapp/src/shared/ui/primitives/index.ts
webapp/src/shared/ui/index.ts
webapp/src/styles/components.css
webapp/src/styles.css
webapp/src/shared/ui/DESIGN_SYSTEM_FOUNDATION.md
```

## Wrapper exports

Dialog:

```text
DialogRoot
DialogTrigger
DialogPortal
DialogOverlay
DialogContent
DialogTitle
DialogDescription
DialogClose
```

Tooltip:

```text
TooltipProvider
TooltipRoot
TooltipTrigger
TooltipContent
```

Popover:

```text
PopoverRoot
PopoverTrigger
PopoverPortal
PopoverContent
PopoverClose
```

DropdownMenu:

```text
DropdownMenuRoot
DropdownMenuTrigger
DropdownMenuContent
DropdownMenuItem
DropdownMenuSeparator
DropdownMenuLabel
```

Tabs:

```text
TabsRoot
TabsList
TabsTrigger
TabsContent
```

Select:

```text
SelectRoot
SelectTrigger
SelectValue
SelectContent
SelectItem
SelectLabel
SelectSeparator
```

## CSS activation

Add:

```text
webapp/src/styles/design-system.css
```

It should import:

```css
@import './styles/tokens.css';
@import './styles/components.css';
```

Then import `design-system.css` from the active `webapp/src/styles.css`.

The new component class names must use the `nd-` prefix to avoid collisions.

## Important decision

Skip `AlertDialog` in this PR because current `main` does not include `@radix-ui/react-alert-dialog`. Add it only when a destructive confirmation flow actually needs it.

## Non-goals

- No page migration.
- No Workspace changes.
- No WebChat admin changes.
- No WebChat widget changes.
- No backend changes.
- No API/auth changes.

## Acceptance

```text
[ ] npm ci passes
[ ] npm run typecheck passes
[ ] npm run lint passes
[ ] npm run build passes
[ ] login visual smoke has no breakage
[ ] dashboard visual smoke has no breakage
[ ] no horizontal overflow introduced
```

## Rollback

Revert this PR. Since no product page should depend on wrappers yet, rollback is low risk.

---

# Fast-Track PR 2 — Runtime Control Tower First Visible Adoption

## Branch

```text
feature/runtime-control-tower-fast-track
```

## Commit target

```text
feat: upgrade runtime page to control tower
```

## Goal

Use the new design system on the lowest-risk operational surface first: `/runtime`.

This produces visible product value quickly while avoiding Workspace/WebChat risk.

## Files to add

```text
webapp/src/features/runtime-control/RuntimeControlRoute.tsx
webapp/src/features/runtime-control/components/RuntimePageHeader.tsx
webapp/src/features/runtime-control/components/RuntimeHealthCard.tsx
webapp/src/features/runtime-control/components/OpenClawBridgePanel.tsx
webapp/src/features/runtime-control/components/JobsPanel.tsx
webapp/src/features/runtime-control/components/ProductionReadinessPanel.tsx
webapp/src/features/runtime-control/components/SafetyGatePanel.tsx
webapp/src/features/runtime-control/index.ts
webapp/src/entities/runtime/types.ts
webapp/src/entities/runtime/queryKeys.ts
webapp/src/entities/runtime/index.ts
```

## Files to update

```text
webapp/src/routes/runtime.tsx
```

## Components to use

```text
Card
CardHeader
CardBody
Badge
StatusBadge
SafetyGateBadge
Tooltip
Tabs
```

## Implementation rules

- Route remains `/runtime`.
- Existing API calls remain unchanged unless wrapped with type-safe local helpers.
- No backend behavior change.
- No dangerous runtime mutation changes.
- Health states must clearly show healthy / degraded / failing / unknown.

## Acceptance

```text
[ ] /runtime loads
[ ] existing runtime data still appears
[ ] status labels remain semantically correct
[ ] typecheck/lint/build pass
[ ] no API path changes
```

## Rollback

Revert PR to previous `runtime.tsx` route.

---

# Fast-Track PR 3 — AI Governance Studio Fast Foundation

## Branch

```text
feature/ai-governance-studio-fast-track
```

## Commit target

```text
feat: upgrade AI Control to governance studio foundation
```

## Goal

Refactor current AI Control into an AI Governance Studio shell and apply the design system in one PR.

This is high product value and lower operational risk than Workspace.

## Files to add

```text
webapp/src/features/ai-governance/AiGovernanceRoute.tsx
webapp/src/features/ai-governance/components/GovernanceHeader.tsx
webapp/src/features/ai-governance/components/ConfigTypeTabs.tsx
webapp/src/features/ai-governance/components/ConfigResourceList.tsx
webapp/src/features/ai-governance/components/ConfigEditorPanel.tsx
webapp/src/features/ai-governance/components/DraftPublishedStateCard.tsx
webapp/src/features/ai-governance/components/VersionHistoryPanel.tsx
webapp/src/features/ai-governance/components/PolicyGuardrailSummary.tsx
webapp/src/features/ai-governance/index.ts
```

## Files to update

```text
webapp/src/routes/ai-control.tsx
```

## Components to use

```text
Tabs
Card
Badge
StatusBadge
SafetyGateBadge
Tooltip
Select
Dialog
```

## Implementation rules

- Route remains `/ai-control`.
- Existing AI config APIs remain unchanged.
- Existing create/update/publish/rollback behavior remains unchanged.
- JSON mode remains available.
- No auto-reply enablement.
- No backend schema change.

## Acceptance

```text
[ ] AI config list loads
[ ] draft edit/save works
[ ] publish works
[ ] rollback works
[ ] version history still displays
[ ] typecheck/lint/build pass
```

## Rollback

Revert route extraction and return to previous `ai-control.tsx` implementation.

---

# Fast-Track PR 4 — WebChat Control Center Fast Foundation

## Branch

```text
feature/webchat-control-center-fast-track
```

## Commit target

```text
feat: upgrade WebChat admin to control center foundation
```

## Goal

Upgrade WebChat admin UI into a modular control center without touching the public widget runtime.

## Files to add

```text
webapp/src/features/webchat-admin/WebChatAdminRoute.tsx
webapp/src/features/webchat-admin/components/WebChatInbox.tsx
webapp/src/features/webchat-admin/components/WebChatThreadPanel.tsx
webapp/src/features/webchat-admin/components/WebChatReplyComposer.tsx
webapp/src/features/webchat-admin/components/WebChatSafetyPanel.tsx
webapp/src/features/webchat-admin/components/WebChatSnippetCard.tsx
webapp/src/features/webchat-admin/components/WebChatRuntimeNotes.tsx
webapp/src/features/webchat-admin/index.ts
```

## Files to update

```text
webapp/src/routes/webchat.tsx
```

## Components to use

```text
Card
Badge
StatusBadge
SafetyGateBadge
Tooltip
Popover
Tabs
Dialog
```

## Implementation rules

- Route remains `/webchat`.
- Admin conversation list behavior remains unchanged.
- Thread polling remains unchanged.
- Reply API behavior remains unchanged.
- Safety gate flags remain unchanged.
- Snippet output remains unchanged.
- Do not change `backend/app/static/webchat/widget.js`.
- Do not change public WebChat APIs.

## Acceptance

```text
[ ] WebChat admin conversation list loads
[ ] thread loads
[ ] reply still works
[ ] safety/review UI still appears
[ ] snippet display still works
[ ] typecheck/lint/build pass
```

## Rollback

Revert route extraction and return to previous `webchat.tsx` implementation.

---

# Fast-Track PR 5 — WebChat Widget SDK Parallel Runtime

## Branch

```text
feature/webchat-widget-sdk-parallel-runtime
```

## Commit target

```text
feat: add parallel WebChat widget SDK runtime
```

## Goal

Build the next WebChat SDK runtime in parallel without cutting over production snippet yet.

This is the fastest safe way to move toward TypeScript + Shadow DOM without breaking current embeds.

## Files to add

```text
packages/webchat-core/README.md
packages/webchat-core/src/index.ts
packages/webchat-core/src/config.ts
packages/webchat-core/src/transport.ts
packages/webchat-core/src/messageModel.ts
packages/webchat-widget/README.md
packages/webchat-widget/src/index.ts
packages/webchat-widget/src/mount.ts
packages/webchat-widget/src/shadowRoot.ts
packages/webchat-widget/src/widgetApp.ts
packages/webchat-widget/src/styles.ts
packages/webchat-widget/vite.config.ts
docs/frontend-upgrade/webchat-widget-sdk-implementation-plan.md
```

## Files to update

```text
webapp/package.json or root package/build scripts if needed
```

## Generated output target

```text
backend/app/static/webchat/widget.next.js
```

## Implementation rules

- Keep current `backend/app/static/webchat/widget.js` active.
- `widget.next.js` is parallel only.
- Preserve script attribute config semantics.
- Preserve visitor token/conversation model.
- Do not change public WebChat APIs.
- Do not cut over production snippet in this PR.

## Acceptance

```text
[ ] old widget.js untouched
[ ] widget.next.js builds
[ ] basic visitor init/send/poll smoke documented or automated
[ ] typecheck/lint/build pass
```

## Rollback

Delete parallel package and `widget.next.js`. Current widget remains active.

---

# Fast-Track PR 6 — Workspace Ticket Operations Cockpit

## Branch

```text
feature/workspace-cockpit-fast-track
```

## Commit target

```text
feat: upgrade workspace to ticket operations cockpit
```

## Goal

Upgrade the highest-value page after foundations are proven.

This PR is allowed to be larger because time is limited, but it must preserve existing behavior and be easy to revert.

## Files to add

```text
webapp/src/features/workspace/WorkspaceRoute.tsx
webapp/src/features/workspace/components/TicketQueue.tsx
webapp/src/features/workspace/components/TicketQueueFilters.tsx
webapp/src/features/workspace/components/TicketDetailHeader.tsx
webapp/src/features/workspace/components/ConversationTimeline.tsx
webapp/src/features/workspace/components/EvidencePanel.tsx
webapp/src/features/workspace/components/BulletinPanel.tsx
webapp/src/features/workspace/components/AiCopilotPanel.tsx
webapp/src/features/workspace/components/SafetyGatePanel.tsx
webapp/src/features/workspace/components/TicketActionPanel.tsx
webapp/src/features/workspace/index.ts
```

## Files to update

```text
webapp/src/routes/workspace.tsx
```

## Components to use

```text
Card
Badge
StatusBadge
SafetyGateBadge
Tooltip
Popover
Tabs
Dialog
Select
```

## Implementation rules

- Route remains `/workspace`.
- Existing API calls remain unchanged.
- Existing dirty-state protection must remain.
- Existing ticket list/detail behavior must remain.
- Existing workflow update behavior must remain.
- Existing AI intake behavior must remain.
- No external outbound dispatch changes.
- No backend behavior changes.

## Layout target

```text
Queue / Filters
→ Ticket + Conversation + Evidence
→ AI Copilot + Safety + Action Panel
```

## Acceptance

```text
[ ] ticket list loads
[ ] search/filter works
[ ] ticket selection works
[ ] ticket detail loads
[ ] conversation displays
[ ] evidence/attachments display
[ ] workflow update works
[ ] AI intake save works
[ ] dirty-state protection works
[ ] no horizontal overflow
[ ] typecheck/lint/build pass
```

## Rollback

Revert PR to previous `workspace.tsx`. Because backend/API behavior is unchanged, rollback is frontend-only.

---

# Optional Fast-Track PR 7 — Realtime Runtime

## Branch

```text
feature/realtime-runtime-fast-track
```

## Commit target

```text
feat: add realtime runtime with polling fallback
```

## Goal

Add SSE/event runtime only after Runtime, AI, WebChat admin, and Workspace are stable.

## Files likely added

```text
webapp/src/shared/realtime/eventTypes.ts
webapp/src/shared/realtime/eventClient.ts
webapp/src/shared/realtime/useEventStream.ts
webapp/src/shared/realtime/index.ts
backend/app/api/events.py
backend/tests/test_realtime_events.py
```

## Rules

- Polling remains fallback.
- Do not expose admin events to public visitors.
- Events must be authenticated and permission-filtered.
- Duplicate events must be ignored.

## Acceptance

```text
[ ] SSE connects when available
[ ] fallback polling works
[ ] duplicate events ignored
[ ] unauthorized stream rejected
[ ] typecheck/lint/build pass
[ ] backend tests pass
```

---

# Fast-track execution order

Execute in this order:

```text
1. feature/design-system-radix-wrapper-foundation
2. feature/runtime-control-tower-fast-track
3. feature/ai-governance-studio-fast-track
4. feature/webchat-control-center-fast-track
5. feature/webchat-widget-sdk-parallel-runtime
6. feature/workspace-cockpit-fast-track
7. optional feature/realtime-runtime-fast-track
```

## Why this order

```text
Radix wrappers first      = shared UI foundation ready
Runtime second            = low-risk visible adoption
AI Governance third       = high demo value, medium risk
WebChat admin fourth      = high demo value, protects public widget
Widget SDK fifth          = parallel runtime, no cutover risk
Workspace sixth           = highest value and highest risk, done after foundations prove stable
Realtime seventh          = optional because it touches frontend/backend runtime behavior
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

After the fast-track plan:

```text
- NexusDesk-owned Radix wrappers
- active semantic design tokens
- Runtime Control Tower
- AI Governance Studio foundation
- WebChat Control Center foundation
- parallel SDK WebChat widget runtime
- Workspace Ticket Operations Cockpit
- optional realtime runtime with polling fallback
```

This is the fastest responsible path from the current `main` branch to an agent-native customer operations console.
