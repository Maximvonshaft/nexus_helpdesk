# 03 — Target Architecture RFC

## RFC status

Proposed. Implementation must not start until this RFC is reviewed and accepted.

## Problem statement

NexusDesk has evolved from a lightweight helpdesk interface into a multi-module customer operations console. The current React/Vite stack is modern enough, but the frontend architecture is still page-centric. Core route files now combine data loading, mutation orchestration, local form state, business logic, visual layout, and feature UI.

This increases the risk of:

- expensive feature expansion
- hard-to-review pull requests
- repeated UI patterns
- inconsistent safety and AI behavior
- fragile WebChat evolution
- insufficient testability

## Decision summary

The target architecture is a phased, non-rewrite migration.

Accepted direction:

- Keep React 19 + Vite for the authenticated console.
- Keep TanStack Router and TanStack Query.
- Introduce feature/domain/shared layering.
- Introduce a governed design system.
- Convert WebChat from static script to TypeScript SDK runtime while preserving the script embed contract.
- Add a realtime event runtime with fallback polling.
- Upgrade AI Control into AI Governance Studio.
- Keep a future Next.js portal as a separate app, not as a replacement for the authenticated console.

## Non-decision / explicit non-goals

- Do not rewrite the console in Next.js.
- Do not replace FastAPI backend assumptions.
- Do not break existing WebChat snippet behavior.
- Do not alter production API contracts without explicit API review.
- Do not introduce external outbound dispatch in this frontend upgrade.

## Target repository structure

```text
webapp/src/
  app/
    providers/
    router/
    shell/
    bootstrap/
  features/
    workspace/
    webchat-admin/
    ai-governance/
    runtime-control/
    channel-accounts/
    users-admin/
    bulletins/
  entities/
    ticket/
    conversation/
    customer/
    channel/
    ai-config/
    user/
    runtime/
  shared/
    api/
    auth/
    ui/
    layout/
    realtime/
    hooks/
    schemas/
    telemetry/
    utils/
  styles/
    tokens.css
    base.css
    themes.css
    globals.css
```

## Layer rules

### app

Owns app bootstrapping, providers, router registration, global shell wiring, and global error boundaries.

May import from:

- features
- entities
- shared

Must not contain deep business rendering logic.

### features

Owns user-facing business workflows such as Workspace, WebChat Admin, AI Governance, Runtime Control.

May import from:

- entities
- shared

Must not import from other features except through explicitly approved public APIs.

### entities

Owns reusable domain concepts such as Ticket, Customer, Conversation, Channel, AIConfig, User, Runtime.

May import from:

- shared

Must not import from features.

### shared

Owns reusable infrastructure and generic UI.

Must not import from features or entities.

## API architecture

Current `webapp/src/lib/api.ts` should be split gradually.

Target shape:

```text
shared/api/httpClient.ts
shared/api/errors.ts
shared/api/authToken.ts
entities/ticket/api.ts
entities/conversation/api.ts
entities/ai-config/api.ts
entities/channel/api.ts
entities/runtime/api.ts
features/workspace/api.ts when feature-level orchestration is required
```

Rules:

- Request wrapper remains centralized.
- Domain APIs are typed and grouped.
- Public visitor WebChat APIs and authenticated admin APIs remain separate.
- 401 handling remains globally consistent.
- API URLs remain environment-driven.

## State management architecture

### Server state

Use TanStack Query.

Rules:

- All remote data should use query/mutation hooks.
- Query keys should be centralized per domain.
- Realtime events should update or invalidate query cache through typed handlers.

### Local UI state

Use component-local React state by default.

Introduce Zustand/Jotai only for cross-page UI state such as:

- shell panel state
- command palette state
- global event dock state
- possibly user preference state

### Form state

Keep page-local form state for simple forms.

For complex forms, introduce explicit feature hooks and validation schemas.

### Realtime state

Use an event client that can:

- connect through SSE
- deduplicate events
- reconnect with backoff
- fallback to polling
- update TanStack Query caches through typed handlers

## UI architecture

Target component split:

```text
shared/ui/primitives
shared/ui/feedback
shared/ui/navigation
shared/ui/forms
shared/ui/overlays
shared/ui/data-display
shared/ui/business
```

Business components include:

- TicketStatusBadge
- PriorityBadge
- ChannelBadge
- SLABadge
- SafetyGateBanner
- EvidenceCard
- AIInsightCard
- RuntimeHealthBadge
- ConversationBubble

## Styling architecture

Current monolithic `styles.css` should be split gradually.

Target:

```text
styles/tokens.css
styles/base.css
styles/themes.css
styles/globals.css
```

Rules:

- CSS variables define semantic tokens.
- Component styling should live near the component when practical.
- Business status colors must use semantic tokens rather than ad-hoc hex values.
- Dark mode and density mode should be token-ready even if not shipped in Phase 1.

## WebChat architecture

Target packages:

```text
packages/webchat-core/
packages/webchat-widget/
packages/webchat-react/
```

Rules:

- Preserve one-line script embed.
- Build widget through Vite library mode.
- Use Shadow DOM for style isolation.
- Do not depend on host website React.
- Maintain compatibility with existing public API until migration is explicitly approved.
- Add origin allowlist and channel configuration through separate reviewed backend work.

## AI Governance architecture

AI Control should evolve into AI Governance Studio.

Target modules:

- Persona Studio
- Knowledge Studio
- SOP Builder
- Policy Guardrail
- Version Diff
- Sandbox Test
- Rollback Center

Rules:

- Existing draft/publish/rollback model remains.
- JSON editing remains available for technical users.
- Business form mode should be added for non-technical operators.
- Published config must remain distinct from draft config.

## Realtime architecture

Target endpoint concept:

```text
GET /api/events/stream
```

Target frontend modules:

```text
shared/realtime/eventClient.ts
shared/realtime/eventTypes.ts
shared/realtime/useEventStream.ts
shared/realtime/handlers.ts
```

Rules:

- Prefer SSE first.
- Use fallback polling for unsupported or failed realtime connections.
- Keep existing polling during migration until realtime stability is proven.
- Events must be typed and deduplicated.

## Migration strategy

Use Strangler Fig migration:

1. Add new structure without changing behavior.
2. Move shared utilities and API client infrastructure.
3. Extract components from current route modules.
4. Replace route internals feature by feature.
5. Keep old behavior reachable until replacement is validated.
6. Add feature flags for high-risk UI replacements.

## Risk register

| Risk | Impact | Mitigation |
|---|---:|---|
| Large route refactor breaks business flow | High | Extract components incrementally with smoke tests |
| WebChat SDK breaks customer embeds | High | Preserve old script contract, dual-run if needed |
| Realtime events duplicate updates | Medium | Event id dedupe and idempotent cache handlers |
| AI governance UI allows bad config | High | Schema validation, sandbox test, review gate |
| Design system causes visual regression | Medium | Snapshot/visual smoke for key pages |
| API split changes auth behavior | High | Preserve shared request wrapper and 401 behavior |

## Approval checklist

- Product accepts target product shape.
- Frontend accepts layer rules and migration order.
- Backend accepts API compatibility constraints.
- Security accepts WebChat and AI boundaries.
- QA accepts test strategy and smoke coverage.
- Operations accepts release and rollback approach.

## Final recommendation

Proceed with planning approval. Do not begin implementation until execution epics and acceptance criteria are reviewed.
