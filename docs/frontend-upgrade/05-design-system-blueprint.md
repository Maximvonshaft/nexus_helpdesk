# 05 — Design System Blueprint

## Status

Proposed. This document defines the design system direction before implementation begins.

## Design goal

Create a coherent enterprise operations console visual system that feels:

- precise
- calm
- fast
- AI-assisted
- operationally serious
- suitable for customer support, logistics operations, and runtime monitoring

The design should not be decorative-first. Motion and polish are allowed only when they improve orientation, confidence, or operational clarity.

## Current style baseline

The current app uses a large global stylesheet with CSS variables and component classes. It already has a coherent baseline but is too centralized for long-term growth.

## Target design language

Reference qualities:

- Linear-like clarity
- Cursor-like command surface
- Intercom-like customer support friendliness
- Datadog-like operational status clarity

Design keywords:

```text
structured
agentic
quiet confidence
high information density
clear hierarchy
action-oriented
```

## Token model

Create semantic design tokens, not only raw colors.

Recommended token groups:

```text
--color-bg
--color-surface
--color-surface-raised
--color-border
--color-border-strong
--color-text
--color-text-muted
--color-brand
--color-accent
--color-success
--color-warning
--color-danger
--color-info
--color-ai
--color-channel-webchat
--color-channel-whatsapp
--color-channel-telegram
--color-channel-email
--shadow-panel
--shadow-popover
--radius-sm
--radius-md
--radius-lg
--radius-xl
--space-1 ... --space-10
```

## Theme readiness

Phase 1 may ship light mode only, but tokens should support:

- light mode
- dark mode
- compact density
- comfortable density

Do not hardcode status colors inside feature components.

## Component layers

### Primitive components

Located under:

```text
shared/ui/primitives/
```

Required primitives:

- Button
- IconButton
- Input
- Textarea
- Select
- Checkbox
- Switch
- Badge
- Card
- Tabs
- Dialog
- Sheet
- Popover
- Tooltip
- Toast
- Command
- Table
- Skeleton
- EmptyState
- Alert

### Business components

Located under:

```text
shared/ui/business/
```

Required business components:

- TicketStatusBadge
- PriorityBadge
- ChannelBadge
- SLABadge
- SafetyGateBanner
- EvidenceCard
- AIInsightCard
- RuntimeHealthBadge
- ConversationBubble
- CustomerIdentityCard
- OpenClawStatusCard
- WebChatSnippetBlock

## Accessibility requirements

Components must support:

- keyboard operation
- visible focus states
- aria labels where needed
- dialog focus trap
- no color-only status communication
- semantic buttons instead of clickable divs
- screen-reader-safe realtime announcements

## Motion rules

Motion is allowed for:

- panel open/close
- command palette
- toast entry/exit
- AI suggestion streaming indicator
- new message arrival highlight
- runtime warning pulse with reduced-motion support

Motion is not allowed for:

- blocking workflow clarity
- decorative distractions
- heavy page transitions that slow operators down

## Layout primitives

Introduce explicit layout components:

- AppShell
- Sidebar
- TopBar
- PageHeader
- SplitPane
- RightContextPanel
- EventDock
- PanelStack
- ResponsiveStack

## Data display patterns

For queues and tables:

- use virtualized rendering when item counts exceed 200
- use compact density for operations-heavy pages
- keep selected item visible
- preserve scroll position when possible
- avoid layout jumps during refresh

## Form patterns

All forms should support:

- label
- hint
- validation message
- disabled state
- dirty state
- submit loading state
- destructive action confirmation

## AI-specific design patterns

AI outputs should never look identical to verified system facts.

Use separate visual treatments for:

- AI suggestion
- verified evidence
- human-reviewed approval
- safety blocked state
- low-confidence state
- missing evidence state

## Safety-specific design patterns

Safety gate states:

```text
safe
needs_review
blocked
unsupported_fact
sensitive_content
policy_violation
```

Each state must show:

- label
- reason
- recommended next action
- whether override is possible

## WebChat widget design tokens

Widget should support runtime-configurable tokens:

- brand color
- surface color
- text color
- border radius
- title
- subtitle
- assistant name
- welcome message
- locale
- density

Widget token mapping must be isolated from admin console tokens.

## Design system acceptance criteria

- No duplicate button/card/form implementations in new code.
- Status colors use semantic tokens.
- All dialogs and command surfaces are keyboard accessible.
- Main console has no default horizontal overflow.
- Business components clearly separate AI suggestions from verified facts.
- WebChat visual system is configurable and isolated.
