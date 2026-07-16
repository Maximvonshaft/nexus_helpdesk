# Nexus MUI Visual Replacement Implementation Plan

> Work Item: #753  
> Exact starting main: `1fd011e8153833f77ca6d1d469071af5db4afb0c`  
> Sole branch: `work/753-canonical-ui-interaction-refinement`  
> Sole PR: #754

## Goal

Replace the visible shell of the existing canonical Nexus operator console with Material UI while preserving the functional machine and ending with exactly one production visual system.

The final state is not “MUI plus old CSS.” The final state is:

`MUI components + one Nexus MUI theme + Nexus domain composition`

The replaced custom generic components, route CSS, token CSS and Radix Dialog dependency must be deleted before merge.

## Selected authority

Machine decision: `webapp/design/mui-visual-authority.v1.json`.

Pinned stack:

- `@mui/material@9.2.0`;
- `@mui/icons-material@9.2.0`;
- `@emotion/react@11.14.0`;
- `@emotion/styled@11.14.1`;
- `react-is@18.3.1`;
- `package.json` override `react-is: 18.3.1`.

MUI owns generic controls, surfaces, typography, layout, dialogs, notices, loading, lists, tables and navigation presentation. Nexus owns only domain-specific composition such as Case Spine, evidence items and operational-result summaries.

## Non-duplication method

Every task follows:

`identify current consumers -> replace with MUI -> preserve behavior -> delete replaced implementation -> scan residue`

The old and new visual systems may coexist only inside the unmerged branch during active migration. Partial merge, runtime switches, V2 routes and indefinite compatibility are forbidden.

## Preserved machine

Do not change unless a separate accepted defect proves it necessary:

- backend API contracts;
- authorization and server-owned scope;
- queue truth;
- business state mapping;
- drafts and deep links;
- mutation safety;
- confirmation requirements;
- error and degraded behavior.

## Task 1 — Lock the MUI authority

**Files**

- Create: `webapp/design/mui-visual-authority.v1.json`
- Create: `webapp/tests/mui-visual-authority-contract.test.mjs`
- Create: `docs/engineering/mui-visual-migration.md`
- Modify: `webapp/scripts/assert-frontend-architecture.mjs`
- Modify: #753 and PR #754 descriptions

**Requirements**

- MUI 9.2.0 is the only selected broad UI framework.
- Emotion is the only styling engine.
- React Is is pinned to React 18.3.1.
- Tailwind, shadcn, Ant Design, Chakra, Mantine, Bootstrap and other broad frameworks remain forbidden.
- MUI packages not explicitly approved remain forbidden as direct dependencies.

**Commit**

`docs(frontend): select MUI as replacement visual authority`

## Task 2 — Install the exact package set

**Files**

- Modify: `webapp/package.json`
- Regenerate: `webapp/package-lock.json`

**Required package state**

```json
{
  "dependencies": {
    "@mui/material": "9.2.0",
    "@mui/icons-material": "9.2.0",
    "@emotion/react": "11.14.0",
    "@emotion/styled": "11.14.1",
    "react-is": "18.3.1"
  },
  "overrides": {
    "react-is": "18.3.1"
  }
}
```

Do not remove Radix or old visual dependencies in this task. They are removed only after all current consumers migrate.

**Verification**

```bash
cd webapp
npm install --ignore-scripts
npm run architecture
npm run typecheck
```

**Commit**

`build(frontend): install pinned MUI visual stack`

## Task 3 — Establish one MUI theme root

**Files**

- Create: `webapp/src/theme/nexusTheme.ts`
- Create: `webapp/src/theme/NexusThemeProvider.tsx`
- Modify: `webapp/src/main.tsx`
- Add focused theme contract tests

**Theme responsibilities**

- palette;
- typography;
- spacing;
- shape;
- density;
- breakpoints;
- transitions;
- z-index;
- component defaults;
- component overrides;
- CSS variables;
- high-contrast and reduced-motion compatibility.

Use `ThemeProvider` and `CssBaseline` once at the application root. No nested route themes.

The approved visual direction remains “Dense calm logistics cockpit”: restrained surfaces, limited color, compact operational density and no decorative gradients, glass or glow.

**Commit**

`feat(frontend): establish single Nexus MUI theme`

## Task 4 — Migrate Login and AppShell

**Files**

- Modify: `webapp/src/routes/login.tsx`
- Modify: `webapp/src/app/AppShell.tsx`
- Modify: `webapp/src/app/AppNavigation.tsx`
- Modify: `webapp/src/app/navigation.ts`
- Delete after migration: `webapp/src/styles/auth.css`
- Delete after migration: `webapp/src/app/app-shell.css`

**MUI components**

- AppBar / Toolbar;
- Box / Stack / Container;
- Button / IconButton;
- TextField;
- Tabs or navigation buttons where semantically appropriate;
- Typography;
- Menu / Tooltip for compact actions.

Preserve authentication, navigation, scope, user identity, logout and router behavior.

Use operator-facing names:

- `工作台` -> `案例处理`;
- `知识` -> `知识与流程`;
- `运行与审计` -> `系统运行`;
- `运营总览` -> `运营监控`.

**Commit**

`refactor(frontend): migrate login and shell to MUI`

## Task 5 — Migrate the canonical Workspace

**Files**

- Modify in place: `webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx`
- Modify domain presentation helpers only where needed to feed MUI props
- Delete after migration: `operator-workspace.css`
- Delete after migration: `operator-workspace-refinements.css`

**Structure**

```text
scoped queue | dominant continuous case surface | contextual next-action rail
```

**MUI composition**

- List / ListItemButton for queue rows;
- Box / Stack / Divider for continuous case structure;
- Typography for hierarchy;
- Chip only for true compact labels;
- Alert for warning, degraded and failure feedback;
- LinearProgress / CircularProgress / Skeleton for loading;
- Dialog for confirmation;
- Accordion or Collapse for technical disclosure;
- TextField / Select / Button for actions;
- Drawer or responsive Tabs for mobile structure.

**Functional invariants**

- queue selection;
- deep links;
- reply drafts;
- stale selection protection;
- authorization;
- cancel preview binding;
- mutation loading and duplicate-submit prevention;
- error/degraded behavior.

**Visual requirements**

- Case identity, owner, urgency and blocker are immediately visible.
- Case Spine uses only durable/server-provided truth.
- Evidence, communication and results form one continuous work surface.
- Technical IDs are behind disclosure.
- Badge overload is eliminated through MUI Typography, Chip, Alert and numeric text.

**Commit sequence**

- `refactor(frontend): migrate workspace queue and case header to MUI`
- `feat(frontend): compose truthful MUI case spine`
- `refactor(frontend): migrate workspace actions and communication to MUI`

No intermediate commit is mergeable independently.

## Task 6 — Migrate supporting routes

Migrate in this order:

1. `/knowledge`;
2. `/channels`;
3. `/runtime`;
4. `/control-tower`;
5. boundary and not-found pages.

Use MUI directly for generic controls and layout. Keep only domain-specific composition in Nexus components.

Delete after each route reaches parity:

- `admin-routes.css`;
- `knowledge.css`;
- `runtime-evidence-audit.css`.

Control Tower should use action-oriented lists or tables rather than decorative KPI card grids.

**Commits**

One coherent migration commit per route domain on the same branch.

## Task 7 — Remove the old generic component system

After all active consumers use MUI, delete:

- `webapp/src/components/ui/Button.tsx`;
- `ButtonLink.tsx`;
- `Badge.tsx`;
- `Field.tsx`;
- `EmptyState.tsx`;
- `ErrorSummary.tsx`;
- `TechnicalDetails.tsx`;
- `ConfirmDialog.tsx`;
- `PageHeader.tsx`.

Remove all imports and exports referencing them.

Delete:

- `webapp/src/styles/components.css`;
- `webapp/src/styles/tokens.css` after the theme fully owns tokens;
- remaining route visual CSS;
- `@radix-ui/react-dialog` from package.json and lockfile.

Reduce `styles.css` and `a11y.css` to narrowly justified browser/accessibility rules, or delete them if MUI plus semantic HTML fully covers their responsibilities.

**Commit**

`refactor(frontend): retire replaced custom visual system`

## Task 8 — Residue and authority enforcement

Strengthen architecture checks to fail if the completed migration contains:

- imports from deleted generic UI files;
- route CSS imports;
- `--nd-*` visual token usage;
- old `.nd-*` generic component classes;
- Radix Dialog;
- any broad UI framework other than MUI;
- nested themes or second theme files;
- raw feature palettes;
- V2 routes or runtime switches.

Change the MUI authority status from `authorized_not_installed` to `complete` only after these checks pass.

**Commit**

`test(frontend): enforce completed MUI-only visual authority`

## Task 9 — Verification and browser acceptance

```bash
cd webapp
npm run architecture
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e

cd ..
python scripts/verify_repository.py --static-only
python scripts/verify_repository.py --focused-backend --skip-browser
python scripts/verify_repository.py
```

Browser evidence must cover:

- 375, 768, 1024 and 1440 widths;
- keyboard-only completion;
- visible focus and dialog focus restoration;
- reduced motion;
- browser zoom and text enlargement;
- long identifiers and customer content;
- loading, empty, degraded, stale, conflict, repair and error states;
- large queue and evidence/message lists;
- deterministic screenshots of representative Workspace states.

## Merge policy

PR #754 remains Draft throughout migration.

Do not merge until one unchanged exact Head proves:

- all routes use MUI;
- one MUI theme owns visual tokens;
- old generic components are deleted;
- old route CSS is deleted;
- Radix is removed;
- no second framework or switch exists;
- functional behavior is preserved;
- all local and browser verification passes;
- independent review is current on that exact Head.
