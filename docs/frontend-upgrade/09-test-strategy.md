# 09 — Test Strategy

## Status

Proposed. This document defines the minimum testing strategy before implementation starts.

## Test principle

Every phase of the upgrade must protect existing production flows first, then prove new behavior.

No implementation phase is complete until it can show:

- type safety
- lint/build success
- core flow smoke evidence
- relevant regression coverage
- rollback confidence

## Required baseline commands

Frontend:

```bash
cd webapp
npm run typecheck
npm run lint
npm run build
```

Backend:

```bash
pytest backend/tests
```

These are minimum gates, not sufficient final gates.

## Test layers

### Static checks

Purpose:

- prevent type drift
- catch obvious syntax and lint violations
- ensure production build still works

Required:

- TypeScript typecheck
- ESLint
- Vite production build

### Unit tests

Purpose:

- verify pure business utilities
- verify API response normalization
- verify safety-state mapping
- verify design-system component helpers

Candidates:

- formatters
- query key builders
- event dedupe helpers
- WebChat config parser
- AI config schema validation

### Component tests

Purpose:

- verify key components in isolation

Priority components:

- TicketStatusBadge
- SafetyGateBanner
- EvidenceCard
- AIInsightCard
- ConversationTimeline
- WebChatSnippetBlock
- CommandPalette

### API contract smoke

Purpose:

- ensure frontend assumptions match backend responses

Core contracts:

- auth
- ticket list
- ticket detail
- workflow update
- AI intake
- WebChat conversations
- WebChat thread
- WebChat reply
- AI configs
- runtime health

### E2E smoke

Purpose:

- prove user-visible flows work end to end

Minimum flows:

1. Login.
2. Open Workspace.
3. Search/filter ticket list.
4. Open ticket detail.
5. Save workflow update.
6. Save AI intake.
7. Open WebChat admin.
8. Open conversation thread.
9. Send WebChat reply.
10. Open AI Control.
11. Save AI config draft.
12. Publish AI config.
13. Roll back AI config.
14. Open Runtime.
15. Verify runtime health section renders.

### WebChat embed smoke

Purpose:

- ensure the widget works on a host page independent of the admin console

Minimum checks:

- one-line snippet loads
- launcher appears
- panel opens
- init API is called
- visitor message sends
- message list loads
- conversation persists after reload
- widget does not create horizontal overflow
- mobile viewport works

Future SDK-specific checks:

- Shadow DOM exists
- host CSS does not break widget
- widget CSS does not affect host page
- config parser handles missing/invalid attributes

### Accessibility smoke

Minimum checks:

- keyboard navigation for shell and command palette
- visible focus states
- dialog/sheet focus trap
- labels on form controls
- no color-only status states
- message timeline does not spam screen readers

Recommended tooling:

- axe-core or Playwright accessibility checks

### Visual regression

Initial target pages:

- login
- dashboard
- workspace
- webchat admin
- AI control
- runtime
- WebChat demo page

Visual regression can start as screenshot smoke before full automated diff is introduced.

### Performance checks

Minimum:

- build output size inspected
- WebChat widget size measured
- no route chunk growth without explanation
- long queues should be virtualized when item count exceeds threshold
- polling interval impact reviewed before adding new polling

## Feature-specific test expectations

### Frontend runtime foundation

- App routes still render.
- API client behavior unchanged.
- Auth expiration behavior unchanged.
- No visual changes expected except intentional low-risk cleanup.

### Design system

- Existing pages still render.
- Buttons, cards, inputs, badges, dialogs remain accessible.
- No default horizontal overflow.
- Status semantics preserved.

### Workspace cockpit

- Selecting ticket works.
- Dirty state protection works.
- Refresh does not overwrite unsaved edits.
- Evidence, bulletin, conversation, and action panels render correctly.
- Save mutations update UI and invalidate queries.

### WebChat runtime SDK

- Old snippet compatibility works.
- Widget initializes on demo host page.
- Message send/fetch works.
- Shadow DOM isolation works when enabled.
- Visitor token flow remains valid.

### Realtime event runtime

- SSE connects when available.
- Fallback polling works when SSE fails.
- Duplicate events are ignored.
- Query cache invalidation works.
- Disconnection shows reconnecting state.

### AI Governance Studio

- Create/update/publish/rollback still work.
- Invalid JSON/schema is blocked.
- Sandbox test renders result.
- Draft/published states are distinct.
- Version diff is visible before publish.

## Release smoke checklist

After deployment:

```text
[ ] /healthz OK
[ ] /readyz OK
[ ] admin login works
[ ] Workspace loads
[ ] Ticket detail opens
[ ] WebChat admin loads
[ ] WebChat demo widget loads
[ ] WebChat visitor message can be sent
[ ] Admin WebChat reply works
[ ] AI Control loads
[ ] Runtime loads
[ ] No critical browser console errors
```

## Test evidence requirement

Every implementation PR must include:

- commands run
- pass/fail result
- screenshots for UI changes
- known gaps
- rollback notes

## Acceptance rule

A PR that changes production behavior must not be merged without at least baseline static checks and targeted smoke evidence for the affected user flow.
