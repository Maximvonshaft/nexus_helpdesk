# Radix primitives adoption runbook

This branch starts the controlled Radix UI adoption phase.

## Why Radix

NexusDesk should not hand-roll complex interaction primitives such as dialogs, popovers, dropdown menus, tooltips, tabs, select controls, sheets, and alert dialogs. These controls require correct focus management, keyboard navigation, ARIA semantics, portal behavior, escape handling, and layering.

Radix primitives are the target base layer for complex interaction behavior. NexusDesk will wrap Radix in local shared UI components so the application consumes NexusDesk-owned APIs rather than raw third-party primitives everywhere.

## Dependency rule

Do not hand-edit `package-lock.json`.

When Radix packages are introduced, generate the lockfile through npm from inside `webapp/`:

```bash
npm install \
  @radix-ui/react-dialog \
  @radix-ui/react-popover \
  @radix-ui/react-tooltip \
  @radix-ui/react-dropdown-menu \
  @radix-ui/react-tabs \
  @radix-ui/react-select
```

Then commit both:

- `webapp/package.json`
- `webapp/package-lock.json`

The existing Frontend CI must continue to run `npm ci`, `npm run typecheck`, `npm run lint`, and `npm run build`.

## First wrapper targets

The first Radix-backed wrappers should be:

- Dialog
- AlertDialog
- Tooltip
- Popover
- DropdownMenu
- Tabs
- Select

Do not connect all wrappers to business pages in the same PR.

## Adoption order

1. Add Radix dependencies with generated lockfile.
2. Add local wrapper components under `shared/ui/primitives`.
3. Add component styles using NexusDesk tokens.
4. Smoke test the wrappers in isolation.
5. Adopt one low-risk admin/status surface.
6. Do not touch Workspace or WebChat admin until wrapper behavior is proven.

## Public API rule

Feature code should import NexusDesk wrappers:

```ts
import { Dialog, Tooltip } from '@/shared/ui'
```

Feature code should not import raw Radix primitives directly except inside local wrapper implementation files.

## Rollback rule

If Radix integration causes CI or runtime issues:

1. Revert the wrapper PR.
2. Remove unused Radix dependencies through npm.
3. Re-run `npm ci`, typecheck, lint, and build.

## Non-goals for the first Radix PR

- No Workspace redesign.
- No WebChat admin redesign.
- No active behavior change in core workflows.
- No external WebChat widget changes.
- No backend changes.
