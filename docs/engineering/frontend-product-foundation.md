# Frontend Product Foundation — Active Engineering Authority

## Authority

The Nexus operator frontend completed source and product convergence through merged PR #748. Work Item #753 and Draft PR #754 replace the former custom visual layer with one Material UI authority.

Active authorities:

- Product register: `webapp/PRODUCT.md`
- Design register: `webapp/DESIGN.md`
- Machine foundation: `webapp/design/frontend-product-foundation.v1.json`
- MUI decision: `webapp/design/mui-visual-authority.v1.json`
- Application source: `webapp/`
- Application shell: `webapp/src/app/AppShell.tsx`
- Navigation registry: `webapp/src/app/navigation.ts`
- Visual theme: `webapp/src/theme/nexusTheme.ts`
- Root visual provider: `webapp/src/theme/NexusThemeProvider.tsx`
- Generic visual components: `@mui/material@9.2.0`
- Icons: `@mui/icons-material@9.2.0`
- Styling engine: Emotion
- Operational status language: `webapp/src/domain/operationalPresentation.ts`
- HTTP transport: `webapp/src/lib/apiClient.ts`
- Canonical operator route: `/workspace`

Historical custom components, custom tokens, route CSS and donor branches are evidence only. They are not implementation authority and must not be restored.

## Current code state

The unmerged #754 branch now has one MUI-based operator product:

- `/workspace` remains the sole case-work product spine;
- `/knowledge`, `/channels`, `/runtime` and `/control-tower` remain supporting domains inside the same AppShell;
- `/webchat` remains compatibility redirect only;
- `ThemeProvider` and `CssBaseline` are mounted exactly once by `NexusThemeProvider`;
- `nexusTheme.ts` is the only visual token, component-default, focus, motion, shape and elevation authority;
- active routes render generic controls directly from MUI;
- the former `webapp/src/components/ui/` generic component authority is deleted;
- custom token, shared-component, authentication, AppShell, Workspace, Knowledge, administration and runtime-audit stylesheets are deleted;
- `styles.css` contains browser foundations only;
- `a11y.css` contains only the bounded `.sr-only` utility;
- GitHub Actions remain retired and `.github/workflows` must remain absent.

Code migration is not production acceptance. `package-lock.json` still requires exact regeneration and the unchanged final Head must pass local tests, build and browser acceptance before merge.

## Non-duplication invariant

Every frontend change follows:

`existence audit -> canonical owner -> migrate all consumers -> delete replaced implementation -> residue scan`

No parallel implementation is permitted.

Forbidden:

- V2 or redesigned copies of current routes;
- another AppShell or navigation graph;
- another ThemeProvider or `createTheme` owner;
- another generic component library;
- custom generic Button, Field, Select, Chip, Dialog, Empty, Error, Tabs, Table or navigation systems;
- route-private palettes or component CSS;
- old/new runtime switches;
- Tailwind, shadcn/ui, Ant Design, Chakra UI, Mantine, Bootstrap, PrimeReact or Semantic UI;
- compatibility code without named consumers and same-delivery deletion criteria.

Nexus components may exist only for product-specific concepts such as Case Spine, queue task row, evidence item, operational result and closure state. They must compose MUI primitives and may not recreate generic controls.

## Route ownership

| Route | Product job | UI responsibility |
|---|---|---|
| `/login` | Establish operator identity | One direct accessible MUI login flow |
| `/workspace` | Resolve governed logistics cases | Queue, evidence, ownership, action, result, communication and lifecycle |
| `/knowledge` | Maintain approved knowledge and operating guidance | Search, edit, review, publish and retrieval verification |
| `/channels` | Manage channel accounts and onboarding/repair work | Health, configuration tasks and bounded technical detail |
| `/runtime` | Inspect technical readiness and audit evidence | Runtime state and progressive technical disclosure |
| `/control-tower` | Review workload, risk and management actions | Actionable management evidence with canonical drill-down |
| `/webchat` | Compatibility | Redirect to `/workspace`; no product UI |

Navigation labels are operator language. Route registration remains centralized in `webapp/src/app/navigation.ts` and `webapp/src/router.tsx`.

## Product-state ownership

The frontend renders state; it does not invent business truth.

Keep visibly distinct:

- authoritative evidence;
- customer claim;
- approved knowledge or policy;
- AI recommendation or history;
- human decision;
- system event;
- action receipt;
- customer-notification receipt;
- closure and observation state.

The UI must preserve the difference between requested, accepted, queued, technical completion, operational completion, customer notification, business result confirmation and repair required.

HTTP 200, Job `done`, message `sent`, Dispatch `dispatched` or Ticket `closed` cannot be presented as safe business completion.

## Case Spine

The Case Spine remains the product signature:

`Scope -> Evidence -> Decision -> Action -> Operational result -> Customer notification -> Closure / observation`

It may render only durable or server-provided facts. Missing facts display an explicit unavailable or incomplete state. They are never guessed from labels, colors, source status or local component state.

## MUI visual policy

- Use MUI primitives directly for generic presentation.
- Use `nexusTheme.ts` for palette, typography, spacing, shape, elevation, component defaults, focus and motion.
- Use `sx` or theme-aware composition for page layout.
- Do not recreate generic CSS classes.
- Green is reserved for verified success.
- Operational colors require text or another non-color cue.
- Prefer continuous surfaces, sections, lists, dividers and tables over nested cards.
- Use elevation only for true floating surfaces.
- Keep technical identifiers behind progressive disclosure.
- Motion must communicate state, remain within the theme duration scale and respect reduced motion.
- Do not use generic gradients, glassmorphism, decorative glow, excessive rounding or page-load choreography.

## Architecture gates

`webapp/scripts/assert-frontend-architecture.mjs` must fail for:

1. retired frontend, custom component, custom token or route CSS paths;
2. an unreachable production source file;
3. a parallel UI/V2 route or source path;
4. another navigation owner;
5. generic HTTP transport outside `apiClient.ts`;
6. another `createTheme`, ThemeProvider or CssBaseline owner;
7. another UI framework dependency;
8. unapproved MUI or Emotion direct packages;
9. stale `package-lock.json` root dependencies;
10. unused runtime dependencies;
11. restored GitHub Actions.

## Verification

Required on one unchanged exact Head:

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

Browser evidence must cover 375, 768, 1024 and 1440 widths; keyboard-only use; focus and dialog restoration; reduced motion; zoom and text enlargement; long content; loading, empty, unavailable, degraded, conflict and repair states; and large queues/lists.

A successful build alone is not acceptance. PR #754 remains Draft until dependency lock regeneration, exact-head local verification and independent review are complete.

## Rollback

This is a frontend-only migration. A normal Git revert restores the previous coherent source state. It must not require database downgrade, Provider cleanup, queue replay, customer communication or production-data repair.
