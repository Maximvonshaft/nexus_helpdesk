# NexusDesk Frontend Agentic Runtime Upgrade — Execution Readiness Package

Status: planning / review gate only. This package does not authorize implementation yet.

## Purpose

This directory is the execution-readiness package for upgrading NexusDesk from a functional React helpdesk console into an agent-native customer operations runtime.

The package exists to make sure product intent, architecture, UX, security, testing, migration, release, and rollback are reviewed before implementation starts.

## Current main-branch facts

- Main console is a Vite React webapp under `webapp/`.
- The frontend stack currently uses React 19, Vite, TypeScript, TanStack Router, TanStack Query, and Tailwind CSS.
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

## Non-goals for this planning branch

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
5. Upgrade Workspace into a ticket operations cockpit.
6. Upgrade WebChat into an embeddable runtime SDK.
7. Add realtime event runtime.
8. Upgrade AI Control into AI Governance Studio.
9. Harden tests, release, rollback, and observability.
