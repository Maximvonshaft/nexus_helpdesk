# Nexus OSR Frontend Product Foundation — Design Specification

**Work Item:** #613  
**Frontend audit:** #611  
**Baseline:** `main@6faeb50a65b64f84816833a924d3793a498eff96`  
**Status:** Approved implementation specification for an additive contract slice

## 1. Problem

The current webapp contains useful operator, Knowledge, channel and Runtime surfaces, but it does not have one product/design authority explaining what the frontend is, who it serves, how its routes are organized, which visual system is authoritative, or how operational states should be named.

The implementation currently carries overlapping authorities:

- `webapp/src/styles/tokens.css` and `components.css` introduce an `nd-*` semantic foundation;
- `webapp/src/styles.css` contains a legacy/global token and component vocabulary;
- `webapp/src/features/support-console/support-console.css` contains a third private visual vocabulary;
- shared React primitives still bind primarily to legacy class names;
- the only authenticated primary route is `/webchat`, while the product domain is broader than a conversation inbox.

Without an accepted product/design contract, #525 can easily build a fourth UI system or preserve incorrect semantics such as treating Ticket `closed`, Job `done`, message `sent`, or Dispatch `dispatched` as business closure.

## 2. Product thesis

Nexus OSR is a **case-resolution cockpit for multi-country logistics operations**.

Its primary interface job is to let an authorized operator answer five questions without switching mental models:

1. What case am I responsible for?
2. Which evidence is authoritative, stale, unavailable, contradictory, or merely a customer claim?
3. What must happen next, and which actions am I allowed to take?
4. What actually happened after an action was requested?
5. Can this issue be safely completed, observed, reopened, or repaired?

The frontend is not primarily a chatbot UI, generic admin template, Knowledge CMS, or Runtime diagnostics console. Those are supporting domains around the case journey.

## 3. Users and operating environment

Primary users work in a dense, time-sensitive logistics operations environment, usually on desktop screens during long shifts under ordinary office lighting.

- **Support Agent:** triages and owns cases, checks evidence, communicates and performs low-risk governed actions.
- **Team Lead:** manages queue risk, takeover, reassignment and escalations.
- **Operations Manager:** reviews workload, SLA risk, closure quality and country/channel performance.
- **Knowledge/SOP Steward:** maintains approved customer Knowledge and internal operating guidance.
- **Channel Administrator:** manages channel/account health and configuration.
- **Runtime/Audit Operator:** inspects technical evidence without widening customer-data access.

The interface therefore favors calm density, predictable controls, fast keyboard use, explicit states and bounded detail over decorative storytelling.

## 4. Information architecture

The canonical route domains are:

| Domain | Canonical route | Primary job | Authority dependency |
|---|---|---|---|
| Authentication | `/login` | Establish operator identity | Auth |
| Operator work | `/workspace` | Queue, case, evidence, ownership, actions, communication and closure target | #525/#526/#587 |
| Knowledge/SOP | `/knowledge` | Govern customer Knowledge and later internal SkillBank | #568/#529/#530 |
| Channels | `/channels` | Channel/account configuration and health | #547/#571 |
| Runtime & audit | `/runtime` | Technical readiness, debug/eval and bounded audit evidence | #496/#549 |
| Management | `/control-tower` | Tenant-scoped workload, risk, outcome and management drill-down | #527/#528 |

`/webchat` is transitional. It may redirect or preserve a compatibility view during migration, but it is not the canonical product spine.

Navigation visibility is capability- and scope-derived. Hiding a route never substitutes for backend authorization.

## 5. Visual direction

### 5.1 Thesis

**Dense calm logistics cockpit.**

The design should feel like a trusted operating instrument: precise, restrained and fast. Information hierarchy is created through grouping, alignment, type weight and state language—not decorative gradients, oversized cards or novelty controls.

### 5.2 Signature element

The single memorable element is the **Case Spine**: a compact operational rail that represents the real sequence:

`Scope → Evidence → Decision → Action → Operational result → Customer notification → Closure/observation`

The Case Spine is not ornamental. It must expose missing, blocked, contradictory and repair-required stages and eventually consume #585/#587/#526 contracts. It may first exist only as a documented pattern; #525 owns runtime implementation.

### 5.3 Color strategy

Use a restrained product palette. State colors are reserved for meaning.

| Role | Value | Use |
|---|---|---|
| Ink | `#172033` | Primary text and strong controls |
| Canvas | `#F5F7FB` | Application background |
| Surface | `#FFFFFF` | Work surfaces |
| Line | `#D7DEE7` | Dividers and control borders |
| Selection / information | `#1D4ED8` | Current selection, focus and information |
| Operational accent | `#C2410C` | Limited Speedaf/exception emphasis; white text is permitted |
| Success | `#067647` | Verified successful state |
| Warning | `#B54708` | At-risk or attention state |
| Danger | `#B42318` | Blocked, unsafe or failed state |
| AI assistance | `#6D28D9` | AI recommendation/history only, never fact authority |

No state may be conveyed by color alone. The current orange `#F06423` with white normal text is not an approved pair.

### 5.4 Typography and data

- One familiar sans family is authoritative: Inter with system fallbacks.
- Use a fixed product type scale rather than fluid display typography.
- Body copy is at least 14px on dense desktop surfaces and 16px for mobile/form inputs where platform zoom behavior requires it.
- Use tabular figures for SLA clocks, counts, timestamps and identifiers.
- Operator prose should remain within 65–75 characters where paragraph reading is required.
- Interface labels use plain, active language from the operator's perspective.

### 5.5 Spacing, shape and elevation

- Base spacing grid: 4px, with primary rhythm on 8px increments.
- Radius scale: 6px / 8px / 12px. Pills are reserved for compact statuses and segmented selection.
- Shadows are rare and indicate elevation or temporary overlays only.
- Prefer dividers, whitespace and alignment over nested cards.
- No arbitrary z-index values; use a semantic scale.

### 5.6 Motion

- 150–220ms for state transitions.
- Motion communicates selection, ownership, dispatch progress, confirmation, conflict or repair.
- Do not animate page entry or every section.
- Prefer transform/opacity; avoid layout-property animation.
- Every animation has a `prefers-reduced-motion` alternative.

## 6. Component authority

The target authority is:

- semantic tokens: `webapp/src/styles/tokens.css`;
- React primitives: `webapp/src/components/ui/`;
- feature styles consume semantic tokens and primitives rather than inventing palettes/components.

Every interactive primitive must define:

`default, hover, focus, active, selected, disabled, loading, error`

as applicable.

The migration is staged:

1. Inventory overlapping tokens/classes and map them to the target vocabulary.
2. Make shared React primitives consume the semantic authority.
3. Migrate #525 canonical Workspace components first.
4. Migrate admin/Knowledge/Runtime surfaces by route domain.
5. Retire legacy classes and `frontend/` only after #573 parity evidence.

No big-bang stylesheet rewrite is authorized by #613.

## 7. Operator-facing state language

The UI must not collapse unrelated states.

### 7.1 Source and ownership

- Ticket open / resolved / closed
- Unassigned / assigned / handoff requested / handoff accepted

These are source or ownership states, not business completion.

### 7.2 Evidence

- Authoritative and current
- Stale
- Unavailable
- Contradictory
- Customer claim
- Approved Knowledge/policy
- AI recommendation/history

The term `记忆证据` is prohibited for customer/case context. Use `案例证据`, `事实与依据`, or a more specific evidence class. Short-lived Case Context is not long-term customer memory.

### 7.3 Action and outcome

- Requested
- Accepted
- Technical completion
- Operational completion
- Customer notified
- Business result confirmed
- Repair required

A queued Job, HTTP 200, sent message or dispatched Outbox row cannot be presented as business success.

### 7.4 Closure

- Closure blocked
- Observation period
- Eligible to close
- Safely closed
- Reopened

`已结束` is not valid for a Ticket `resolved/closed` unless #585/#587/#526 closure evidence is satisfied.

## 8. Accessibility and quality floor

- WCAG AA contrast: 4.5:1 normal text, 3:1 large text and UI components where applicable.
- Minimum interactive target: 44×44 CSS pixels for primary/touchable controls.
- Visible keyboard focus and logical tab order.
- Semantic forms, landmarks, headings and status announcements.
- No hover-only action.
- Structural responsive behavior at representative 375 / 768 / 1024 / 1440 widths.
- Text enlargement must not hide actions or force horizontal scrolling.
- Loading, empty, unavailable, degraded, conflict and repair states are first-class.
- Browser verification must cover keyboard, slow network, large lists and responsive layouts—not only mocked happy paths.

## 9. Anti-patterns

Do not use:

- generic gradient application backgrounds as identity;
- glassmorphism as a default surface;
- endless same-size card grids;
- hero-metric patterns for operator tasks;
- repeated uppercase eyebrow labels as section scaffolding;
- decorative numbered sections unless the content is truly sequential;
- nested cards;
- raw hex values inside feature styles after migration;
- mixed button/form-control vocabularies;
- internal implementation identifiers in the primary operator layer;
- decorative motion or image hover animation.

## 10. Delivery boundaries

This specification authorizes only an additive contract slice:

- `webapp/PRODUCT.md`
- `webapp/DESIGN.md`
- a machine-readable frontend foundation contract
- architecture/contract tests
- integration/migration documentation

Runtime routes, components, CSS and customer/operator behavior remain unchanged. #525, #564 and #573 consume this specification for implementation.