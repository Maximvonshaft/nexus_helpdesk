# NexusDesk Frontend Agentic Runtime Upgrade — Execution Readiness Package

Status: planning / review gate. This package establishes the governance baseline for implementation. It does not itself change runtime behavior.

## Purpose

This directory is the execution-readiness package for upgrading NexusDesk from a functional React helpdesk console into an agent-native customer operations runtime.

The package exists to make sure product intent, architecture, UX, API contracts, security, testing, migration, release, rollback, and minimal-grain execution plans are reviewed before implementation starts.

## Current main-branch facts

- Main console is a Vite React webapp under `webapp/`.
- The frontend stack currently uses React 19, Vite, TypeScript, TanStack Router, TanStack Query, Tailwind CSS, and Radix primitive dependencies.
- The current route surface includes dashboard, workspace, webchat, bulletins, AI control, control plane, accounts, users, and runtime pages.
- WebChat is currently served as a static browser widget at `backend/app/static/webchat/widget.js`.
- The WebChat admin and Workspace pages are already production-shaped but should be modularized before large-scale feature expansion.

## Execution-readiness gates

Implementation may start only after these documents are reviewed and accepted:

1. `01-current-state-audit.md`
2. `02-product-requirements.md`
3. `03-target-architecture-rfc.md`
4. `04-ux-interaction-blueprint.md`
5. `05-design-system-blueprint.md`
6. `06-webchat-runtime-blueprint.md`
7. `07-ai-governance-blueprint.md`
8. `08-security-threat-model.md`
9. `09-test-strategy.md`
10. `10-execution-epics.md`
11. `11-engineering-handoff.md`
12. `12-acceptance-criteria.md`
13. `13-api-contract-map.md`
14. `14-migration-plan.md`
15. `15-release-rollout-plan.md`
16. `16-rollback-plan.md`
17. `17-pr26-professional-review-report.md`
18. `18-execution-construction-blueprint.md`

## Construction blueprint

`18-execution-construction-blueprint.md` converts the approved frontend direction into minimal, reviewable construction units.

It defines the exact execution order from the current `main` branch:

```text
Radix wrappers
→ Design System CSS activation
→ Runtime low-risk adoption
→ Runtime Control Tower
→ AI Governance Studio
→ WebChat Control Center
→ WebChat SDK runtime
→ Workspace Ticket Operations Cockpit
→ Realtime Event Runtime
→ Branch governance cleanup
```

Each construction unit defines:

- branch name
- commit message
- files to add/update
- implementation details
- non-goals
- acceptance checks
- rollback plan
- hard stop rules

## Non-goals for planning branches

- No production code changes.
- No framework migration.
- No backend API breaking changes.
- No WebChat public API behavior changes.
- No database migration.
- No production deployment.

## Decision baseline

The professional upgrade path is not a rewrite. The target is a phased migration:

1. Freeze and audit current state.
2. Approve target architecture.
3. Establish frontend runtime foundation.
4. Introduce design system and business components.
5. Wrap Radix primitives behind NexusDesk-owned components.
6. Adopt the design system on low-risk runtime surfaces first.
7. Upgrade AI Governance, WebChat, and Workspace only after foundations are proven.
8. Harden tests, release, rollback, and observability.

## Closed checklist

- PR #26 has a complete execution-readiness document set.
- API contract map has been added.
- Migration plan has been added.
- Release rollout plan has been added.
- Rollback plan has been added.
- Professional review report has been added with an `Approve` decision for planning merge.
- Frontend runtime foundation has been merged.
- Agentic design system foundation has been merged.
- Radix adoption runbook has been merged.
- Radix primitive dependencies have been merged.
- Minimal-grain frontend construction blueprint has been added.

## Next branch after construction blueprint merge

After `18-execution-construction-blueprint.md` is merged to `main`, create:

```text
feature/radix-dialog-wrapper
```

First implementation commit target:

```text
feat: add Dialog primitive wrapper
```

The first wrapper phase must not change product behavior or adopt the wrapper in business pages yet.
