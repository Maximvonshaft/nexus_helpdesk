# Nexus MUI Visual Migration — Retirement Evidence

## Purpose

This document records the final disposition of the visual layer that existed on `main@1fd011e8153833f77ca6d1d469071af5db4afb0c` before Work Item #753.

The business machine was preserved. The former custom visual system was replaced by Material UI and physically removed from the sole implementation branch.

Machine-readable evidence:

- `webapp/design/ui-visual-inventory.v1.json`
- `webapp/design/mui-visual-authority.v1.json`
- `webapp/design/frontend-product-foundation.v1.json`

## Final decision

Material UI is the sole generic visual component authority:

- `@mui/material@9.2.0`
- `@mui/icons-material@9.2.0`
- `@emotion/react@11.14.0`
- `@emotion/styled@11.14.1`
- `react-is@18.3.1`

The sole theme and provider are:

- `webapp/src/theme/nexusTheme.ts`
- `webapp/src/theme/NexusThemeProvider.tsx`

`ThemeProvider` and `CssBaseline` are mounted once at the application root.

## Preserved functional responsibilities

The migration did not intentionally change:

- backend APIs;
- authorization and server-owned work scope;
- queue truth and business state contracts;
- drafts and deep links;
- confirmation requirements;
- mutation safety;
- loading, error and degraded behavior.

## Retired visual authorities

The following custom token, component and route-style authorities are physically absent from the MUI branch:

### Custom token and shared component styling

- `webapp/src/styles/tokens.css`
- `webapp/src/styles/components.css`
- `webapp/src/components/ui/`

### Route visual styles

- `webapp/src/styles/auth.css`
- `webapp/src/app/app-shell.css`
- `webapp/src/features/operator-workspace/operator-workspace.css`
- `webapp/src/features/operator-workspace/operator-workspace-refinements.css`
- `webapp/src/features/admin-routes/admin-routes.css`
- `webapp/src/features/knowledge/knowledge.css`
- `webapp/src/features/runtime/runtime-evidence-audit.css`

### Custom generic components

- Button and ButtonLink;
- Badge;
- Field, Input, Select and Textarea wrappers;
- EmptyState and ErrorSummary;
- TechnicalDetails;
- ConfirmDialog;
- PageHeader.

The Radix Dialog dependency was removed after dialog consumers migrated to MUI.

## Bounded remaining CSS

Only two source stylesheets remain:

- `webapp/src/styles.css` — document and browser foundations only;
- `webapp/src/a11y.css` — the `.sr-only` screen-reader utility only.

MUI component overrides, colors, spacing, typography, shape, elevation, focus and reduced motion belong in `nexusTheme.ts`, not in route CSS.

## Root findings and resolutions

### VIS-001 — Badge overload

Previously one pill expressed source, priority, owner, SLA, refresh, counts and outcomes.

Resolution:

- MUI Chip is used only for compact status or metadata;
- counts use Typography and tabular numerals;
- operational states include text and a non-color cue.

### VIS-002 — Nested card grammar

Previously all domains defaulted to bordered rounded containers inside other bordered containers.

Resolution:

- Workspace uses continuous queue, case and action regions;
- Knowledge uses list, editor and verification regions;
- Channels and Control Tower use tables, task rows and sections;
- Runtime uses facts, alerts and progressive disclosure.

### VIS-003 — Incomplete feedback states

Previously empty, error and technical detail states were incomplete or route-private.

Resolution:

- MUI Alert, CircularProgress, Accordion and Dialog provide shared behavior;
- empty states are route-bounded compositions of MUI primitives;
- the custom generic feedback component layer was deleted.

### VIS-004 — Workspace selector drift

Previously current JSX and old Workspace selectors no longer matched.

Resolution:

- Workspace rendering was rebuilt with MUI;
- both Workspace stylesheets were deleted;
- the architecture gate forbids their return.

### VIS-005 — Missing Case Spine

Previously the case journey was represented indirectly through headings, panels and badges.

Resolution:

The Workspace now renders:

`Scope -> Evidence -> Decision -> Action -> Operational result -> Customer notification -> Closure / observation`

Only available durable facts are shown. Missing closure facts are explicitly marked unavailable rather than inferred.

### VIS-006 — Distributed visual vocabulary

Domain mapping helpers remain responsible for business labels and tones:

- `domain/operationalPresentation.ts`;
- `lib/supportStatus.ts`;
- `lib/operatorWorkspacePresentation.ts`.

Their generic rendering now uses MUI and the single Nexus theme.

### VIS-007 — Inconsistent operator terminology

Navigation now uses:

- 案例处理;
- 知识与流程;
- 渠道管理;
- 系统运行;
- 运营监控.

Technical identifiers are secondary or progressively disclosed.

### VIS-008 — Independent runtime-audit styling

The minified runtime-audit stylesheet was replaced by MUI lists, facts, forms, alerts and disclosure, then deleted.

## Route migration results

| Route | MUI migration | Preserved behavior |
|---|---|---|
| `/login` | Complete in code | Authentication and error focus |
| `/workspace` | Complete in code | Queue, deep links, drafts, actions, authorization, confirmations and refresh |
| `/knowledge` | Complete in code | Search, selection, draft guard, edit, publish and retrieval test |
| `/channels` | Complete in code | Account health and onboarding-task lifecycle |
| `/runtime` | Complete in code | Runtime reads, metrics and evidence audit |
| `/control-tower` | Complete in code | Management evidence and canonical drill-down |
| `/webchat` | No product UI | Compatibility redirect only |

## Permanent anti-reintroduction rules

`webapp/scripts/assert-frontend-architecture.mjs` rejects:

- the retired custom component directory;
- the retired custom token and route CSS files;
- any source CSS except `styles.css` and `a11y.css`;
- another UI framework;
- another `createTheme`, ThemeProvider or CssBaseline owner;
- V2 routes or old/new switches;
- restored GitHub Actions;
- stale package-lock root dependencies or missing exact MUI package nodes.

## Remaining acceptance

The code migration and deletion work are complete on the Draft branch, but production acceptance is not complete.

Required before PR #754 can leave Draft:

1. regenerate `package-lock.json` from the exact `package.json` manifest;
2. run architecture, lint, typecheck, contract tests and production build on one unchanged Head;
3. run browser evidence at 375, 768, 1024 and 1440 widths;
4. verify keyboard, focus, reduced motion, zoom, long content, degraded states and large lists;
5. obtain independent exact-head review.

No unexecuted check is represented as passing.
