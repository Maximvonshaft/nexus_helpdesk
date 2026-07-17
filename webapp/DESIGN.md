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

Exact palette values live only in `webapp/src/theme/nexusTheme.ts`. This register defines semantic use, not a second token table.

- Primary: current selection, focus and primary action.
- Success: verified successful state only.
- Warning: at risk or requires attention.
- Error: unsafe, blocked or failed.
- Information: neutral operational context.
- Background, surface, text and divider values are owned by the MUI theme.

No feature-level raw colors are permitted. Meaning is never conveyed by color alone, and all combinations must meet WCAG AA.

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

The single visual authority is:

- generic components: `@mui/material@9.2.0`;
- icons: `@mui/icons-material@9.2.0`;
- theme and semantic design values: `webapp/src/theme/nexusTheme.ts`;
- root provider and baseline: `webapp/src/theme/NexusThemeProvider.tsx`;
- bounded operational states: `webapp/src/app/OperatorPresentation.tsx`.

Feature code composes MUI directly. It must not create generic Button, Input, Dialog, Badge or Field wrappers, route-private palettes, route CSS, a second ThemeProvider, or a parallel design system.

Every interactive component implements the applicable default, hover, focus, active, selected, disabled, loading, error and confirmed states through the single theme.

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

Primary interfaces show only the task or section name, current state, relevant facts, blocking reason, recovery path and explicit action.

Use direct operator terms such as `待处理任务`, `当前负责人`, `处理时限`, `接手处理`, `转回待处理`, `恢复自动回复`, `搜索测试` and `系统状态`. Technical identifiers remain behind named disclosures such as `系统信息`, `审计数据`, `原始数据` or `处理编号`.

Product narration, architecture explanations, frontend/backend responsibility text, permission philosophy and AI self-description are prohibited from primary operator surfaces.

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

## Extension rules

1. Extend existing canonical routes and feature modules; do not create V2 or legacy alternatives.
2. Use MUI and the existing Nexus theme; do not add a second framework or token authority.
3. Add domain composition only when MUI cannot express the business concept directly.
4. Keep one API transport, one Workspace state graph and one Knowledge implementation.
5. Update architecture, contract and browser evidence in the same change.
6. Delete superseded code and documentation in the same delivery.
