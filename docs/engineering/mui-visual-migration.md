# Nexus MUI Visual Replacement

## Decision

The repository owner selected Material UI as the replacement visual framework for the Nexus operator frontend.

The decision is not to add MUI beside the current visual system. The decision is to make MUI the only generic visual component authority and delete the replaced custom visual implementation before PR #754 can merge.

Machine authority: `webapp/design/mui-visual-authority.v1.json`.

## Selected stack

- `@mui/material@9.2.0`
- `@mui/icons-material@9.2.0`
- `@emotion/react@11.14.0`
- `@emotion/styled@11.14.1`
- `react-is@18.3.1`
- `package.json` override: `react-is: 18.3.1`

Nexus currently uses React and React DOM `18.3.1`. Material UI v9 supports React 18, but its published package depends on React Is 19; the official MUI installation guidance requires React 18 applications to resolve React Is to the same React version.

## New authority model

### MUI owns generic presentation

Use direct Material UI components for:

- buttons and icon buttons;
- links that visually behave as actions;
- text fields, selects, checkboxes, switches and form feedback;
- chips and badges only where their semantics genuinely fit;
- alerts and notices;
- progress, skeleton and loading states;
- dialogs, drawers, menus, tooltips and popovers;
- tabs and navigation controls;
- lists, tables and pagination;
- typography, dividers, papers and surfaces;
- responsive layout through Box, Stack, Grid and MUI responsive props.

### Nexus owns only domain composition

Nexus-specific components may remain or be introduced only when they represent business concepts rather than generic UI controls. Examples:

- Case Spine;
- case identity header;
- queue task row;
- evidence item;
- operational result summary;
- customer-notification state;
- closure readiness.

These domain components must compose MUI primitives. They must not recreate Button, Field, Dialog, Badge, Alert, Tabs or layout foundations.

## Theme authority

Create exactly one theme:

- `webapp/src/theme/nexusTheme.ts`
- `webapp/src/theme/NexusThemeProvider.tsx`

The application root will use MUI `ThemeProvider` and `CssBaseline`.

The theme owns:

- palette;
- typography;
- spacing;
- shape;
- breakpoints;
- transitions;
- z-index;
- density;
- component default props;
- component style overrides;
- CSS variables.

Do not create a second theme, nested route themes or feature-private palettes.

## What will be deleted

After all consumers migrate, delete the current generic visual components:

- `Button.tsx`;
- `ButtonLink.tsx`;
- `Badge.tsx`;
- `Field.tsx`;
- `EmptyState.tsx`;
- `ErrorSummary.tsx`;
- `TechnicalDetails.tsx`;
- `ConfirmDialog.tsx`;
- `PageHeader.tsx`.

Delete Radix Dialog after the MUI Dialog migration.

Delete route and shared CSS after equivalent MUI rendering is complete:

- token and shared component CSS;
- login CSS;
- application shell CSS;
- Workspace CSS and its refinement patch;
- admin-route CSS;
- Knowledge CSS;
- Runtime audit CSS.

`styles.css` and `a11y.css` may survive only as a minimal, audited browser/accessibility layer for responsibilities that MUI and semantic HTML do not own. The target is no route-private visual CSS.

## Migration sequence

1. Install and pin the exact MUI/Emotion/React Is package set and regenerate `package-lock.json`.
2. Add the single theme provider and CssBaseline at the current root.
3. Migrate login and AppShell to prove typography, navigation, forms and responsive behavior.
4. Migrate the canonical Workspace in one coherent slice while preserving all functional behavior.
5. Migrate Knowledge, Channels, Runtime and Control Tower.
6. Replace all custom generic component imports with direct MUI imports.
7. Delete the generic visual component files, Radix dependency and route CSS.
8. Remove dead class names and imports.
9. Run local architecture, lint, typecheck, tests, build and browser verification.
10. Merge only when the final exact Head contains one visual system.

## Temporary coexistence boundary

MUI and the old visual layer may coexist only inside the unmerged migration branch while route migration is actively taking place.

Forbidden:

- partial merge to `main`;
- old/new runtime switches;
- V2 routes;
- a second AppShell;
- a second component directory;
- two production themes;
- leaving old generic controls for later cleanup;
- adding Tailwind, shadcn, Ant Design, Chakra, Mantine or another framework.

## Styling rule

The owner direction is not to hand-build a replacement CSS framework.

Allowed custom styling is limited to:

- the single MUI theme configuration;
- component-specific `sx` or MUI `styled` usage where a Nexus domain layout requires it;
- minimal global accessibility/browser rules that MUI cannot express;
- business-specific visual composition such as the Case Spine.

Do not hand-build generic buttons, fields, dialogs, status pills, cards, tables, menus, tabs or notifications.

## Acceptance

PR #754 may merge only when:

- every active route renders through MUI;
- the MUI theme is the only visual token authority;
- old generic UI component files are deleted;
- old route visual CSS is deleted;
- Radix Dialog is removed;
- no second UI framework is present;
- no old/new switch or V2 route exists;
- functional behavior is unchanged;
- keyboard, focus, zoom, reduced motion and WCAG AA remain valid;
- 375, 768, 1024 and 1440 browser evidence is recorded;
- one unchanged exact Head passes repository-local verification.
