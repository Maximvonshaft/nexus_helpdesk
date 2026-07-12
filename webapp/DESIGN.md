# Nexus OSR Frontend Design Register

## Visual thesis

**Dense calm logistics cockpit.**

Nexus OSR is used by support and operations teams handling time-sensitive logistics cases during long desktop sessions. The interface should feel like a trusted operating instrument: precise, restrained, fast and legible under normal office lighting.

Familiarity is a feature. The product should not surprise operators with invented controls, decorative motion or marketing-page styling. Its distinctive quality comes from how clearly it represents evidence, action progress and safe closure.

## Signature: Case Spine

The single signature element is the **Case Spine**.

It represents the actual case journey:

`Scope → Evidence → Decision → Action → Operational result → Customer notification → Closure / observation`

The Case Spine is functional, not decorative. It must eventually show:

- current stage;
- missing requirement;
- blocked or contradictory evidence;
- action progress;
- repair-required state;
- observation period;
- safe closure eligibility.

It consumes #585, #587 and #526 contracts when those capabilities are implemented by #525. Until then, it remains a documented product pattern and must not fabricate runtime truth.

## Physical context and density

- Primary use: desktop operations screens, 8-hour shifts, frequent switching between queue and case detail.
- Secondary use: tablet and mobile for triage, communication and urgent takeover—not full dense administration.
- Density is deliberate: compact labels and data are acceptable when grouping, focus and touch behavior remain clear.
- Critical action and state information must remain visible without excessive scrolling.
- Technical evidence is progressively disclosed so it does not compete with the operator task.

## Color strategy

Use a restrained product palette. Accent and state colors are operational signals, not decoration.

| Token role | Value | Intended use |
|---|---|---|
| Ink | `#172033` | Primary text and strong neutral controls |
| Canvas | `#F5F7FB` | Application background |
| Surface | `#FFFFFF` | Primary work surfaces |
| Surface subdued | `#F8FAFC` | Secondary panels and grouped rows |
| Line | `#D7DEE7` | Dividers and controls |
| Line strong | `#B9C5D4` | Selected/interactive borders |
| Selection / information | `#1D4ED8` | Current selection, focus and information |
| Operational accent | `#C2410C` | Restricted Speedaf or exception emphasis |
| Success | `#067647` | Verified success only |
| Warning | `#B54708` | At risk or requires attention |
| Danger | `#B42318` | Unsafe, blocked or failed |
| AI assistance | `#6D28D9` | AI recommendation/history, never fact authority |

### Color rules

- Normal text contrast must satisfy WCAG AA at 4.5:1.
- Large text and non-text UI contrast follow applicable WCAG thresholds.
- No meaning is conveyed by color alone; pair color with text, icon or shape.
- White normal text on `#F06423` is not an approved pair.
- Accent is used for primary action, current selection and meaningful state only.
- Do not introduce raw feature-level hex colors after migration; consume semantic tokens.
- Dark mode is not a default requirement. It requires a separate physical-use justification and complete contrast review.

## Typography

- One family: Inter with system fallbacks.
- Product UI uses a fixed type scale rather than fluid marketing headings.
- Suggested scale: 12 / 13 / 14 / 16 / 18 / 20 / 24 / 30px.
- Body line-height: 1.45–1.65.
- Use tabular figures for timestamps, SLA clocks, counts and identifiers.
- Use sentence case for labels and actions.
- Use `text-wrap: balance` for short headings and `text-wrap: pretty` for prose where supported.
- No display fonts in labels, forms, tables or operator controls.

## Spacing, shape and elevation

- Spacing scale follows 4px increments, with most structure on 8px steps.
- Radius scale: 6px, 8px and 12px.
- Pills are reserved for compact statuses and true segmented selection.
- Avoid nested cards. Prefer one work surface with dividers, alignment and whitespace.
- Shadows indicate real elevation: popover, dialog, command surface or sticky raised layer.
- Define a semantic z-index scale for base, sticky, dropdown, backdrop, dialog, toast and tooltip.

## Layout

### Desktop

The canonical operator layout is a three-part work surface:

```text
┌──────────── scoped queue ────────────┬──────────── case work surface ────────────┬──── contextual rail ────┐
│ ownership / SLA / scenario / source  │ Case Spine                                │ facts / policy           │
│ stable cursor list                   │ evidence + conversation                    │ action outcome           │
│                                      │ current task + communication               │ closure target           │
└──────────────────────────────────────┴────────────────────────────────────────────┴─────────────────────────┘
```

The middle column is dominant. The queue and contextual rail support the case; they do not compete equally for visual attention.

### Tablet and mobile

Responsive behavior is structural:

- queue and case become separate navigable views;
- contextual rail becomes an ordered details section or sheet;
- primary action remains reachable without hidden hover behavior;
- tables become labelled rows only when semantic relationships remain clear;
- no horizontal page scroll;
- use `100dvh` for full-height mobile task views.

## Component authority

The target semantic authority is:

- Tokens: `webapp/src/styles/tokens.css`
- React primitives: `webapp/src/components/ui/`

Feature CSS may arrange primitives but must not create another color, radius, button, badge, field or status system.

Every interactive component defines the states that apply:

- default;
- hover;
- focus;
- active/pressed;
- selected;
- disabled;
- loading;
- error;
- success/confirmed.

Shared primitives must be used consistently across operator, Knowledge, channels, Runtime and management surfaces.

## Interaction

- Minimum interactive target: **44×44** CSS pixels for primary/touch controls.
- Provide feedback within 100ms for a press/tap.
- Disable duplicate async submission and show progress.
- Do not rely on hover.
- Place validation error near the field and provide a summary when multiple errors exist.
- Use native forms, buttons, labels and dialog/popover primitives where they provide correct semantics.
- Preserve deep links and predictable Back behavior.
- One primary action per current task state; secondary actions are visually subordinate.

## Motion

- Use 150–220ms transitions for state changes.
- Prefer transform and opacity.
- Motion communicates selection, ownership, dispatch progress, confirmation, conflict or repair.
- No orchestrated page-load sequence.
- No repeated section fade-in grammar.
- No bounce or elastic easing.
- Every animation has a `@media (prefers-reduced-motion: reduce)` alternative.
- Content is visible without animation; motion never gates access.

## Accessibility

- WCAG AA is the release floor.
- Normal text contrast: 4.5:1 minimum.
- Visible keyboard focus on every interactive control.
- Logical focus order follows the visual/task order.
- Semantic headings and landmarks.
- Inputs use visible labels; placeholder text is not a label.
- Dynamic status changes use appropriate live regions without excessive announcements.
- No color-only status.
- Text enlargement and browser zoom must not hide required actions.
- Keyboard users can complete login, queue selection, case ownership, evidence inspection, action confirmation, communication and navigation.

## Loading, empty and failure states

- Use skeletons for content loading where the structure is known.
- Empty states explain why the surface is empty and the next valid action.
- Unavailable and degraded states preserve the last safe server-confirmed information without pretending freshness.
- Conflict and stale-write states explain what changed and require a refresh/review path.
- `repair_required` remains visible until reconciled.
- A green presentation is reserved for verified successful state, not merely queued or accepted work.

## Operator language

Use plain verbs and domain language:

- `接管案例`, not generic `提交`;
- `创建催派工单`, not `执行操作`;
- `请求已排队`, not `处理成功`;
- `运营已完成`, only with operational evidence;
- `已通知客户`, only with notification evidence;
- `符合安全结案条件`, only with closure evaluation.

Do not use `记忆证据` for Case Context. Prefer `案例证据`, `事实与依据`, or the concrete evidence type.

Technical identifiers such as model name, Runtime trace and Job ID belong behind progressive disclosure or in `/runtime`, not in the primary operator hierarchy.

## Anti-patterns

- **No generic gradient** application background as product identity.
- **No endless card grids** of identical icon/title/text units.
- No glassmorphism as a default surface.
- No hero-metric template for operator tasks.
- No repeated uppercase eyebrow over every section.
- No numbered sections unless the content is a real ordered process.
- No gradient text.
- No decorative side-stripe borders.
- No nested cards.
- No random radii, shadows or icon styles.
- No decorative motion.
- No internal implementation terms in primary operator copy.
- No false closure or false success language.

## Migration direction

1. Inventory current tokens, primitives and feature styles.
2. Map legacy values to semantic tokens.
3. Make shared React primitives consume the semantic authority.
4. Build #525 Workspace using only the accepted authority.
5. Prove touch, keyboard, responsive, degraded and large-list behavior in #564.
6. Migrate supporting route domains.
7. Remove legacy frontend and redundant style authorities through #573.

This register defines the future design authority. It does not claim the current production UI already conforms.