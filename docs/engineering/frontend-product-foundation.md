# Frontend Product Foundation — Active Engineering Authority

## Authority

The Nexus operator frontend completed its source and implementation convergence through merged PR #748. The current refinement Work Item is #753.

The active product and design authorities are:

- Product register: `webapp/PRODUCT.md`
- Design register: `webapp/DESIGN.md`
- Machine contract: `webapp/design/frontend-product-foundation.v1.json`
- Application source: `webapp/`
- Application shell: `webapp/src/app/AppShell.tsx`
- Navigation registry: `webapp/src/app/navigation.ts`
- Semantic tokens: `webapp/src/styles/tokens.css`
- Shared UI primitives: `webapp/src/components/ui/`
- Operational status language: `webapp/src/domain/operationalPresentation.ts`
- HTTP transport: `webapp/src/lib/apiClient.ts`
- Canonical operator route: `/workspace`

Historical migration plans, donor PRs and deleted paths are evidence only. They are not implementation authority and must not be revived.

## Current state

Nexus now has one authenticated operator product:

- `webapp/` is the only operator frontend source;
- `/workspace` is the only case-work product spine;
- `/knowledge`, `/channels`, `/runtime` and `/control-tower` are supporting route domains inside the same AppShell;
- `/webchat` is compatibility-only and cannot mount another operator console;
- `components/ui` is the only shared component authority;
- `tokens.css` is the only palette, spacing, radius, motion and elevation authority;
- `operationalPresentation.ts` is the only cross-route operational outcome vocabulary;
- GitHub Actions are retired and `.github/workflows` must remain absent;
- repository acceptance is local through `python scripts/verify_repository.py` and the `webapp` verification commands.

The remaining work is refinement inside the active authority, not migration to a new frontend.

## Non-duplication invariant

Every frontend change follows this sequence:

`existence audit -> canonical owner -> modify in place -> migrate consumers -> delete replaced expression -> residue scan`

No parallel implementation is permitted.

Do not add:

- a V2 or redesigned copy of a current route;
- another AppShell or navigation graph;
- another component or token directory;
- feature-private Button, Field, Input, Select, Badge, Status, Dialog, Empty or Error systems;
- a second status vocabulary;
- a broad UI framework beside the current React/Radix/token architecture;
- a decorative animation library without a separately justified interaction requirement;
- a compatibility layer without named consumers and a same-delivery deletion condition.

A new primitive is valid only when it owns a missing semantic responsibility. It must live in `webapp/src/components/ui/`, migrate all relevant consumers and remove the superseded expression in the same delivery path.

## Route ownership

| Route | Product job | UI responsibility |
|---|---|---|
| `/login` | Establish operator identity | One direct accessible login flow |
| `/workspace` | Resolve governed logistics cases | Queue, evidence, ownership, action, result, communication and lifecycle |
| `/knowledge` | Maintain approved knowledge and operating guidance | Search, edit, review, publish and retrieval verification |
| `/channels` | Manage channel accounts and onboarding/repair work | Health, configuration tasks and bounded technical detail |
| `/runtime` | Inspect technical readiness and audit evidence | Runtime state and progressive technical disclosure |
| `/control-tower` | Review workload, risk and management actions | Actionable management evidence with canonical drill-down |
| `/webchat` | Compatibility | Redirect to `/workspace`; no product UI |

Navigation labels are operator language, not implementation terminology. Route registration remains centralized in `webapp/src/app/navigation.ts` and `webapp/src/router.tsx`.

## Product-state ownership

The frontend renders state; it does not invent business truth.

### Evidence

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

### Action and outcome

The UI must preserve the difference between:

- requested;
- accepted;
- queued or processing;
- technical completion;
- operational completion;
- customer notification;
- business result confirmation;
- repair required.

HTTP 200, Job `done`, message `sent`, Dispatch `dispatched` or Ticket `closed` cannot be presented as safe business completion.

### Case Spine

The Case Spine is the product's functional signature:

`Scope -> Evidence -> Decision -> Action -> Operational result -> Customer notification -> Closure / observation`

It may render only durable or server-provided facts. Missing contracts must produce an explicit unavailable or incomplete state; they must never be guessed from strings, colors or local component state.

## Shared visual vocabulary

Shared primitives own semantic responsibilities, not just appearance.

Required responsibilities include:

- action buttons with default, hover, focus, active, disabled and loading states;
- metadata badges separate from operational status indicators;
- counts separate from status pills;
- labelled fields, validation and disabled reasons;
- empty, loading, degraded, warning and error states;
- technical disclosure;
- page and section headers;
- toolbars, tabs and list rows where the task requires them;
- dialogs with focus restoration and bounded destructive confirmation.

Feature CSS may arrange these primitives. It may not create another palette, control, status, radius, shadow or motion vocabulary.

## Visual direction

The approved thesis remains **Dense calm logistics cockpit**.

- The middle case surface is dominant; queue and context support it.
- Use restrained neutral surfaces and one clear selection/action accent.
- Use operational colors only for meaningful state.
- Green is reserved for verified success.
- Prefer dividers, alignment and spacing over nested cards.
- Shadows indicate real elevation only.
- Pills are reserved for compact statuses and true segmented selection.
- Technical identifiers remain behind progressive disclosure or in `/runtime`.
- Motion communicates state in 150–220 ms and respects reduced motion.
- Do not use generic gradients, gradient text, glassmorphism, decorative glow, excessive rounding, endless card grids or page-load choreography.

## Current refinement sequence — #753

1. Freeze the current authority in machine contracts and architecture checks.
2. Inventory visible labels, badges, counts, states, cards, panels and feature-private CSS.
3. Complete shared primitives in place.
4. Refine `/workspace` without changing its route or business contracts.
5. Converge supporting routes on the same vocabulary.
6. Remove replaced selectors, components, terminology and dependencies.
7. Verify one unchanged exact Head through local tests and browser evidence.

External skills are instruction sources only. For #753 the governed roles are:

- process: `obra/superpowers` at `d884ae04edebef577e82ff7c4e143debd0bbec99`;
- primary domain: `anthropics/skills` frontend-design at `9d2f1ae187231d8199c64b5b762e1bdf2244733d`, adapted to Nexus authorities;
- accessibility and interaction: `vercel-labs/agent-skills` web-design-guidelines at `f8a72b9603728bb92a217a879b7e62e43ad76c81`;
- verification: `anthropics/skills` webapp-testing at `9d2f1ae187231d8199c64b5b762e1bdf2244733d`.

No external skill may create product scope, architecture authority, runtime dependencies or a second task system.

## Architecture gates

`webapp/scripts/assert-frontend-architecture.mjs` must fail when any of the following appears:

1. a retired frontend or Support Console path;
2. an unreachable production source file;
3. duplicate shared primitive exports;
4. a parallel UI/V2 path or route;
5. another navigation owner;
6. a generic HTTP transport outside `apiClient.ts`;
7. another palette or legacy primitive selector;
8. `transition: all`;
9. an unused runtime dependency;
10. a broad parallel UI framework dependency;
11. a restored `.github/workflows` directory.

The machine contract and its tests must describe the active implementation, not the pre-convergence migration state.

## Verification process

Every implementation slice requires:

1. review against `PRODUCT.md`, `DESIGN.md`, this guide and #753;
2. focused behavior or architecture tests before or with the change;
3. `npm run architecture`;
4. `npm run lint`;
5. `npm run typecheck`;
6. `npm test`;
7. `npm run build`;
8. browser review at 375, 768, 1024 and 1440;
9. keyboard, focus, reduced-motion and text-enlargement review;
10. loading, empty, degraded, conflict, repair and large-list review;
11. a final unreachable-file, unused-selector, dependency and duplicate-authority scan.

Repository-wide final verification remains:

```bash
python scripts/verify_repository.py --static-only
python scripts/verify_repository.py --focused-backend --skip-browser
python scripts/verify_repository.py
```

A successful build alone is not frontend acceptance. The PR stays Draft until one unchanged exact Head has current local verification and independent review.

## Rollback

UI-only slices roll back through normal Git reversion. They must not require database downgrade, Provider cleanup, queue replay, customer communication or production-data repair. Source deletion occurs only after all consumers migrate and the previous coherent commit remains independently restorable.
