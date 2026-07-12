# Nexus OSR Frontend Semantic Foundation — Implementation Plan

**Work Item:** #621
**Design:** `docs/superpowers/specs/2026-07-12-frontend-semantic-foundation-design.md`
**Method:** RED → GREEN → REFACTOR → browser inspection → two-stage review → exact-head verification

## Goal

Make the existing shared React primitives and Login route conform to the merged #613 frontend authority without changing Auth APIs or implementing blocked business surfaces.

## Task 1 — RED contracts

Create before implementation:

- `webapp/tests/frontend-semantic-foundation-contract.test.mjs`
- Login expectations in `webapp/e2e/smoke.spec.ts`
- `.github/workflows/frontend-semantic-foundation-gate.yml`

The static contract must initially fail because:

- shared components do not expose `nd-*` authority;
- Button has no loading/size contract;
- Field wraps the whole group in a label;
- Login is not a form;
- Login has no password visibility control;
- CSS import order lets legacy styles override semantic components;
- Login still uses gradient/glass/over-rounded presentation.

Dedicated Gate:

1. checkout exact PR head with full history;
2. run focused Node contract;
3. install locked webapp dependencies;
4. run all Node tests;
5. typecheck, lint and build;
6. run Login-only Playwright smoke using installed Chrome;
7. run exact diff whitespace check.

Capture the failing contract step before implementation.

## Task 2 — Semantic tokens and import order

Update:

- `webapp/src/main.tsx`
- `webapp/src/styles/tokens.css`
- `webapp/src/styles/components.css`
- Login/global compatibility selectors in `webapp/src/styles.css`
- `webapp/src/a11y.css`

Requirements:

- import semantic shared component CSS after legacy compatibility CSS;
- add bounded tokens for controls, type, motion, focus and z-index;
- shared component CSS contains no raw colors;
- default control target is 44px;
- reduced motion covers new transitions;
- Login consumes semantic tokens, no gradient/glass/28px card.

## Task 3 — Shared primitives

Update:

- `Button.tsx`
- `Badge.tsx`
- `Field.tsx`
- `PageHeader.tsx`
- `ConfirmDialog.tsx`

Requirements:

- semantic classes plus temporary compatibility classes where required;
- Button size/loading API, `aria-busy`, duplicate-submit protection;
- explicit field label association and linked error/help text;
- correct heading level;
- shared Button used by ConfirmDialog cancel action.

## Task 4 — Login implementation

Update `webapp/src/routes/login.tsx`:

- `<main>` and semantic `<form onSubmit>`;
- Enter submission;
- specific loading/error copy;
- focus error on failure;
- password visibility control with `aria-pressed`;
- dark context rail + light sign-in task;
- truthful `Fact → Governed action → Closure` orientation;
- mobile structural stack;
- unchanged Auth API/token/redirect.

## Task 5 — Browser evidence

Update `webapp/e2e/smoke.spec.ts` before GREEN to prove:

- semantic Login heading/form;
- password visibility toggle;
- Enter submission reaches `/webchat` through mocked Auth API;
- unauthenticated redirect remains intact;
- mobile viewport has no horizontal overflow and primary controls meet 44px.

## Task 6 — Review and verification

Specification review:

- follows PRODUCT/DESIGN;
- no generic template presentation;
- signature is truthful and non-runtime;
- no blocked Workspace/business scope;
- Auth behavior unchanged.

Code-quality/design review:

- token and component authority is coherent;
- compatibility classes are intentional and bounded;
- no raw shared-component colors;
- states, semantics, copy, responsive and hardening requirements pass;
- no unnecessary dependency or performance regression.

Before Ready:

- re-read latest main;
- verify exact PR head and changed paths;
- all applicable workflows success;
- no unresolved Review Thread;
- post `AGENT_DELIVERY` with exact evidence.

Merge only with expected head SHA and explicit accepted authority.