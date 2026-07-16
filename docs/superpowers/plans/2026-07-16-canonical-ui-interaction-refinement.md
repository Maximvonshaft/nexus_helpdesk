# Canonical UI and Interaction Refinement Implementation Plan

> Work Item: #753  
> Exact starting main: `1fd011e8153833f77ca6d1d469071af5db4afb0c`  
> Sole branch: `work/753-canonical-ui-interaction-refinement`

## Goal

Refine the existing canonical Nexus operator console into a modern, precise and trustworthy logistics case-resolution cockpit without adding a second implementation authority.

This plan changes the current UI in place. It does not create a V2 route, new application, second component library, second token system, broad UI framework, hidden old/new switch or decorative animation layer.

## Governing method

Every task uses:

`existence audit -> canonical owner -> modify in place -> migrate consumers -> delete replaced expression -> residue scan`

External skill roles are pinned in #753. They provide technique only. `PRODUCT.md`, `DESIGN.md`, the machine foundation contract and current code remain authoritative.

## Baseline findings

1. `webapp/` is already the single operator frontend after merged PR #748.
2. No open PR or discovered branch currently owns the same UI-refinement scope.
3. `webapp/scripts/assert-frontend-architecture.mjs` incorrectly requires a GitHub Actions workflow even though Actions are retired and `.github/workflows` must remain absent.
4. `webapp/design/frontend-product-foundation.v1.json` still marks canonical routes as planned, design-system enforcement as incomplete and legacy style sources as active.
5. `docs/engineering/frontend-product-foundation.md` still describes the pre-#748 migration state and can misdirect future implementers toward compatibility layers.
6. `Badge` is overloaded for status, metadata, priority, source, count and refresh state.
7. `EmptyState`, `ErrorSummary` and `TechnicalDetails` do not yet form a complete, consistently styled semantic state vocabulary.
8. Workspace hierarchy remains card-heavy and lacks a truthful functional Case Spine.
9. Operator-visible copy still includes generic or implementation-oriented terms in several paths.
10. Supporting routes reuse the same panel/card/KPI grammar, reducing product-specific hierarchy.

## Task 1 — Freeze the active authority

**Files**

- Modify: `webapp/scripts/assert-frontend-architecture.mjs`
- Modify: `webapp/design/frontend-product-foundation.v1.json`
- Modify: `webapp/tests/frontend-product-foundation-contract.test.mjs`
- Modify: `docs/engineering/frontend-product-foundation.md`
- Create: this plan

**Changes**

- Replace the stale workflow requirement with a fail-closed Actions-retirement check.
- Expand duplicate primitive detection to the complete shared semantic vocabulary.
- Reject obvious V2/new-UI paths and routes.
- Reject broad parallel UI framework dependencies.
- Mark current canonical routes, tokens, primitives and lifecycle as active.
- Remove deleted legacy sources from the machine contract.
- Record #753 as the active bounded refinement path.

**Verification**

```bash
cd webapp
node --check scripts/assert-frontend-architecture.mjs
node --test tests/frontend-product-foundation-contract.test.mjs
npm run architecture
```

**Commit**

`test(frontend): freeze canonical UI refinement authority`

## Task 2 — Produce the implementation inventory

**Files**

- Create: `webapp/design/ui-refinement-inventory.v1.json`
- Create: `webapp/tests/ui-refinement-inventory-contract.test.mjs`

**Inventory domains**

- visible route and navigation labels;
- buttons and action hierarchy;
- badges, statuses, counts and refresh indicators;
- cards, panels, borders, radii and shadows;
- empty, loading, degraded, warning and error states;
- fields and validation;
- technical disclosures;
- operator-visible implementation terminology;
- feature CSS selectors and raw visual values.

Every entry receives one disposition only:

- `KEEP` — current canonical expression remains;
- `REPLACE` — named canonical replacement and consumer list;
- `DELETE` — no retained product value.

The inventory must not become a second design system. It is a bounded migration ledger and is deleted or archived as evidence after acceptance.

**Commit**

`test(frontend): inventory canonical UI expressions`

## Task 3 — Separate metadata, status and count semantics

**Files**

- Modify: `webapp/src/components/ui/Badge.tsx`
- Create only if inventory proves missing responsibility: `webapp/src/components/ui/StatusIndicator.tsx`
- Create only if inventory proves missing responsibility: `webapp/src/components/ui/Count.tsx`
- Modify: `webapp/src/styles/components.css`
- Modify consumers across current routes
- Add focused component/contract tests

**Rules**

- Badge represents compact metadata only.
- StatusIndicator represents operational state with text and non-color-only cue.
- Count represents quantities without pill styling.
- Refresh/loading uses a bounded progress expression, not a status badge.
- Green is reserved for verified success.
- All replaced Badge uses migrate in the same delivery path.

**Commit**

`refactor(frontend): separate status metadata and count semantics`

## Task 4 — Complete shared feedback and disclosure states

**Files**

- Modify in place: `EmptyState.tsx`, `ErrorSummary.tsx`, `TechnicalDetails.tsx`, `PageHeader.tsx`
- Modify: `webapp/src/styles/components.css`
- Migrate route-private feedback styles and delete superseded selectors

**Required variants**

- loading/skeleton where structure is known;
- empty with reason and next valid action;
- degraded/unavailable preserving last safe information;
- warning requiring attention;
- error with specific recovery action;
- technical detail behind progressive disclosure.

**Commit**

`refactor(frontend): complete canonical feedback states`

## Task 5 — Refine navigation, naming and application shell

**Files**

- Modify: `webapp/src/app/navigation.ts`
- Modify: `webapp/src/app/AppNavigation.tsx`
- Modify: `webapp/src/app/AppShell.tsx`
- Modify: `webapp/src/app/app-shell.css`
- Modify route headings and browser titles

**Target visible names**

- `工作台` -> `案例处理`
- `知识` -> `知识与流程`
- `运行与审计` -> `系统运行`
- `运营总览` -> `运营监控`

Use TanStack Router links rather than raw navigation anchors where route semantics require client navigation.

**Commit**

`refactor(frontend): clarify canonical navigation language`

## Task 6 — Recompose Workspace without a second Workspace

**Files**

- Modify in place: `webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx`
- Modify in place: existing Workspace CSS files
- Extract bounded internal components only inside the existing feature when decomposition improves maintenance; do not create another route or product owner

**Desktop structure**

```text
scoped queue | dominant continuous case surface | contextual next-action rail
```

**Changes**

- Queue becomes a continuous task list rather than isolated card tiles.
- Case header prioritizes identity, owner, urgency and blocker.
- Technical source IDs move behind disclosure.
- Case Spine renders only available durable stages and explicitly marks unavailable data.
- Evidence, conversation and result sections become one continuous work surface with dividers and spacing.
- Right rail presents the current task, disabled reason and one primary action.
- Message styling distinguishes participants without nested-card excess.
- Draft, deep-link, authorization, cancel-preview and mutation behavior remain unchanged.

**Commit sequence**

- `refactor(frontend): clarify workspace queue and case header`
- `feat(frontend): render truthful case spine`
- `refactor(frontend): unify workspace evidence communication and action hierarchy`

## Task 7 — Converge supporting routes

Apply the shared vocabulary in this order:

1. `/knowledge`
2. `/channels`
3. `/runtime`
4. `/control-tower`
5. `/login` and boundary pages

**Rules**

- No route-private control or status system.
- Remove generic KPI-card layouts where action-oriented lists or tables are clearer.
- Keep technical detail secondary.
- Delete replaced selectors with each route migration.

**Commits**

One coherent route-domain commit per route; no parallel redesign branches.

## Task 8 — Motion and interaction polish

Motion is added only where it communicates state:

- selection change;
- panel or disclosure opening;
- loading/progress transition;
- confirmation, conflict or repair feedback.

Use existing CSS transitions where sufficient. Do not add an animation dependency unless a concrete interaction cannot be implemented accessibly and smoothly with the current stack.

Requirements:

- 150–220 ms;
- transform/opacity where possible;
- no bounce, elastic or page-load choreography;
- content visible without animation;
- `prefers-reduced-motion` alternative.

**Commit**

`refactor(frontend): polish stateful interaction motion`

## Task 9 — Browser acceptance and residue deletion

**Verification**

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

Browser evidence covers:

- 375, 768, 1024 and 1440 widths;
- keyboard-only task completion;
- focus and dialog restoration;
- reduced motion;
- browser zoom and text enlargement;
- long identifiers and long customer content;
- loading, empty, unavailable, degraded, stale, conflict and repair states;
- large queue and message/evidence lists;
- deterministic screenshots of representative Workspace states.

Final residue scan must prove:

- no V2 route or source;
- no unused selectors or unreachable modules;
- no duplicate primitive or status owner;
- no retired terminology in primary operator surfaces;
- no broad UI framework dependency;
- no restored GitHub Actions;
- no hidden old/new switch.

## PR policy

Open one Draft PR from `work/753-canonical-ui-interaction-refinement` to `main` after Task 1 establishes a coherent review boundary. Keep it Draft through implementation. Do not merge until one unchanged exact Head has all local verification and independent review evidence.
