# Frontend Product Foundation — Engineering Integration

## Authority

- Work Item: #613
- Frontend audit: #611
- Product register: `webapp/PRODUCT.md`
- Design register: `webapp/DESIGN.md`
- Machine contract: `webapp/design/frontend-product-foundation.v1.json`
- Product/business semantics: #583, #585, #587, #526
- Canonical Workspace: #525
- Scale/accessibility: #564
- Legacy frontend retirement: #573

## Current-state inventory

The current frontend has useful foundations but no single accepted UI authority.

### Semantic foundation

- `webapp/src/styles/tokens.css`
- `webapp/src/styles/components.css`
- `webapp/src/components/ui/`

These are the intended future token and component authorities.

### Active legacy/global vocabulary

- `webapp/src/styles.css`

It defines another root token set, generic app-shell/card/button/table/dialog patterns, gradients, large radii and route-specific WebCall styles.

### Feature-private vocabulary

- `webapp/src/features/support-console/support-console.css`

It defines another palette, radius, segmented-control, message, metric, table and responsive system.

### Authority mismatch

Shared `Button.tsx` renders `.button`, while `components.css` defines `nd-button`. Similar mixed authority exists across Badge, Field and feature-specific controls. This is a staged migration problem, not permission to introduce another component library.

## Integration principle

Use **no big-bang rewrite**.

The accepted sequence is:

1. Keep the product/design contract additive and inactive.
2. Inventory existing classes/tokens/components and map them to semantic roles.
3. Make shared primitives consume the accepted token authority.
4. Implement the new canonical #525 Workspace using only shared primitives and semantic tokens.
5. Prove #564 accessibility, touch, responsive, slow-network, degraded and large-list behavior.
6. Migrate Knowledge, channel, Runtime and management route domains.
7. Remove redundant global/feature authorities and the legacy `frontend/` through #573 after parity evidence.

## Route ownership

| Route domain | Implementation owner | Notes |
|---|---|---|
| `/login` | #613 implementation follow-up / #564 quality | Preserve auth behavior; use semantic form and shared primitives |
| `/workspace` | #525 | Canonical queue-driven operator product spine |
| `/knowledge` | Knowledge/M11 owners, consuming the foundation | Separate configuration from active case work |
| `/channels` | Channel/admin owners, consuming #547/#571 | Capability-scoped administration |
| `/runtime` | M7/M12 owners | Technical evidence behind distinct authority |
| `/control-tower` | #527/#528 | Management analytics and outcome drill-down |
| `/webchat` | #525/#573 transition | Compatibility only; not canonical long term |

## Product-state ownership

### #587 — Action outcome

The frontend may render the shared vocabulary but must not infer it locally. #587 owns reconciliation of:

- requested;
- accepted;
- technical completion;
- operational completion;
- customer notification;
- business result confirmation;
- repair required.

Queued, `done`, `sent`, `dispatched` and HTTP 200 are source states only.

### #526 — Lifecycle

#526 owns:

- closure requirements;
- observation;
- safe closure;
- conflict and stale revision;
- repair reconciliation;
- reopen.

The UI must not use Ticket `resolved/closed` as a substitute.

### #525 — Workspace

#525 consumes the Product, Design and state-language contracts to deliver:

- canonical queue entry;
- Case Spine;
- evidence hierarchy;
- scenario/closure target;
- ownership;
- server-calculated allowed actions;
- durable outcome and customer-notification display.

### #564 — Product quality

#564 owns implementation proof for:

- 44×44 interaction targets;
- WCAG AA contrast;
- keyboard/focus/screen-reader journey;
- reduced motion;
- responsive structural behavior;
- stable cursor pagination;
- slow/unavailable/degraded/conflict/repair states;
- large-list and browser performance;
- visual-regression evidence.

### #573 — Single frontend authority

#573 removes obsolete routes, assets, documentation and fallback behavior only after canonical route parity. It also verifies that Control Tower links resolve to supported modern routes.

## Design-system migration map

### Phase 1 — Authority and inventory

- Product/design contract accepted.
- Enumerate hard-coded feature colors, radii, shadows and duplicated components.
- Record semantic mapping and exceptions.

### Phase 2 — Primitive convergence

- `Button`, `Badge`, `Field`, `Input`, `Select`, `Textarea`, tabs/segments, alerts and empty/loading states consume `--nd-*` tokens.
- Complete applicable component states.
- Do not rename every class in one PR; use compatibility classes or composed class names where needed.

### Phase 3 — Workspace implementation

- Build #525 with the accepted shared primitives.
- Do not copy Support Console private CSS into the new route.
- Introduce no raw feature palette.

### Phase 4 — Supporting surfaces

- Separate Knowledge, channels, Runtime and management routes.
- Apply capability-driven navigation.
- Move internal implementation details out of the primary operator hierarchy.

### Phase 5 — Retirement

- Remove redundant styles only when reference searches and browser evidence show no live consumer.
- Delete legacy frontend behavior through #573.

## Architecture gate

Machine-enforceable checks should eventually verify:

1. `webapp/PRODUCT.md`, `webapp/DESIGN.md` and the machine contract exist and parse.
2. The route-domain registry has unique canonical paths.
3. `tokens.css` and `components/ui` remain the declared authorities.
4. New canonical Workspace feature styles do not introduce raw hex values except explicitly reviewed visualization/data cases.
5. Feature code does not label Ticket closed, Job done, message sent or Dispatch dispatched as safe closure.
6. Primary operator components do not expose prohibited terminology such as `记忆证据`.
7. New buttons/segments meet target-size contracts.
8. Current route links in management responses have a registered frontend destination or explicit transitional mapping.

The first #613 gate validates the authority files. Stronger implementation gates belong with #525/#564/#573 when production code changes begin.

## Review process

Every frontend implementation slice follows:

1. Design/specification review against PRODUCT and DESIGN.
2. Test-first behavior or architecture contract where practical.
3. Exact-head typecheck, lint, unit/contract tests and build.
4. Browser review at representative viewports.
5. Keyboard and focus journey.
6. Contrast and touch-target review.
7. Slow-network, loading, empty, degraded, conflict and repair scenarios.
8. Code-quality/design-system review.

A build passing without these checks is not frontend acceptance.

## Rollback

The #613 foundation is additive. Reverting it removes documentation, the machine contract and the dedicated contract gate. It does not require database downgrade, route rollback, Provider cleanup, queue replay, customer communication or production-data repair.