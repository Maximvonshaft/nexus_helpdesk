# 11 — Engineering Handoff

## Status

Planning handoff. This is not yet authorization to implement. Implementation starts only after the readiness package is reviewed and accepted.

## Task name

NexusDesk Frontend Agentic Runtime Readiness — Execution Planning Package

## Repository

`Maximvonshaft/nexus_helpdesk`

## Planning branch

`planning/frontend-agentic-runtime-readiness`

## Baseline

Baseline commit used for planning:

`d2cbc2012164f895d78f407381f5a103237e42b5`

## Objective

Prepare NexusDesk for a professional, phased frontend upgrade from a production-shaped helpdesk console into an agent-native customer operations runtime.

This handoff creates the review package only. It must not change production behavior.

## Current facts

- Frontend app lives under `webapp/`.
- Current stack includes React 19, Vite, TypeScript, TanStack Router, TanStack Query, and Tailwind CSS.
- Main app entry is `webapp/src/main.tsx`.
- Router is `webapp/src/router.tsx`.
- Large business routes include `workspace.tsx`, `webchat.tsx`, and `ai-control.tsx`.
- Static WebChat widget currently lives at `backend/app/static/webchat/widget.js`.
- API client is centralized in `webapp/src/lib/api.ts`.
- Global style model is centralized in `webapp/src/styles.css`.

## Required documents in this package

- `README.md`
- `01-current-state-audit.md`
- `02-product-requirements.md`
- `03-target-architecture-rfc.md`
- `04-ux-interaction-blueprint.md`
- `05-design-system-blueprint.md`
- `06-webchat-runtime-blueprint.md`
- `07-ai-governance-blueprint.md`
- `08-security-threat-model.md`
- `09-test-strategy.md`
- `10-execution-epics.md`
- `12-acceptance-criteria.md`

## Implementation guardrails

Do not:

- rewrite the console
- migrate authenticated console to Next.js
- change public WebChat API shape
- change backend database schema
- change production deployment topology
- enable external outbound dispatch
- break current one-line WebChat snippet
- remove polling before realtime fallback is proven
- mix all epics into one pull request

## Approved target direction

If readiness is approved, implementation should follow this phased path:

1. Frontend Runtime Foundation
2. Design System Foundation
3. Workspace Ticket Operations Cockpit
4. WebChat Runtime SDK
5. Realtime Event Runtime
6. AI Governance Studio
7. Runtime Control Tower
8. Release Hardening and Documentation

## Required implementation PR template

Every execution PR must include:

```markdown
## Summary

## Scope

## Non-goals

## Changed files

## Screenshots / recordings

## Test evidence

- [ ] npm run typecheck
- [ ] npm run lint
- [ ] npm run build
- [ ] pytest backend/tests
- [ ] targeted smoke

## Risk assessment

## Rollback plan

## API compatibility notes

## Security notes
```

## Required local validation before execution PR

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

## Execution readiness checklist

Implementation may start only after:

- current-state audit accepted
- product requirements accepted
- target architecture RFC accepted
- UX blueprint accepted
- design system blueprint accepted
- WebChat runtime blueprint accepted
- AI governance blueprint accepted
- security threat model accepted
- test strategy accepted
- execution epics accepted
- acceptance criteria accepted

## Recommended first implementation branch after approval

`feature/frontend-runtime-foundation`

## Recommended first implementation commit message

`refactor: establish frontend runtime foundation`

## Recommended final implementation posture

Small, reviewable, rollback-safe PRs. No large rewrite. No production behavior change without test evidence.
