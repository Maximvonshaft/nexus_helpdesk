# Nexus Customer Service Design Authority

## Direction

**Dense, calm customer-service desk.**

The interface should feel like a professional logistics service desk: high information density, restrained visual hierarchy, predictable controls, and clear accountability. It must not resemble a chatbot showcase, generic analytics template, engineering dashboard, or card collection.

## Signature interaction

The customer case is the product spine:

`Customer request → Facts → Ownership → Next action → Operational result → Customer update → Completion or follow-up`

This sequence is visible as a compact progress strip. It is not decorative; each step reflects durable state or an explicit blocker.

## Layout

### Desktop

- Left: scoped customer queue and filters.
- Center: customer request, case facts, progress, and conversation.
- Right: one primary action, operational actions, and result history.
- Header: one product identity and one capability-derived navigation.

### Mobile

Four reachable task views remain available:

- `待办`
- `案例`
- `沟通`
- `处理`

No essential action or result panel is silently hidden. Controls are at least 44×44 CSS pixels.

## Visual hierarchy

1. Customer need and current blocker.
2. Urgency, due time, and ownership.
3. Verified facts and missing information.
4. Next permitted action.
5. Customer conversation.
6. Actual result and follow-up.
7. Technical detail only in privileged, separate system administration.

## Color

All colors come from `src/styles/tokens.css`.

- Neutral canvas and white work surfaces.
- Dark ink for primary actions and active navigation.
- Blue for focus and informational emphasis.
- Orange for operational attention.
- Green only for confirmed positive state.
- Red only for failure, overdue, or destructive actions.

Raw feature colors are prohibited.

## Components

`src/components/ui/` is the only shared component authority.

Required shared vocabulary:

- Button
- Badge
- Field, Input, Select, Textarea
- Page header
- Card
- Empty state
- Error summary
- Confirmation dialog
- Bounded detail disclosure

Feature code may compose these primitives but must not recreate a second button, form, status, dialog, or navigation system.

## Interaction rules

- One main action per current task state.
- Destructive actions require explicit confirmation.
- Unsaved replies and knowledge changes block accidental navigation.
- Loading, empty, unavailable, stale, conflict, and repair states are explicit.
- Success wording requires durable business evidence.
- Reduced motion is respected.
- Focus order follows the operator task sequence.
- No color-only meaning.

## Anti-patterns

- No competing operator consoles.
- No visible internal automation or model terminology.
- No raw JSON or internal job identifiers in normal customer-service screens.
- No endless card grids.
- No generic gradients or oversized marketing hero sections.
- No hidden disabled controls without an explanation.
- No duplicate palettes or legacy class vocabularies.