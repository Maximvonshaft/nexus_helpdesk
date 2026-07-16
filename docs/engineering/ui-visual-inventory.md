# Nexus Canonical UI Visual Inventory

## Purpose

This document records the visual layer that exists on `main@1fd011e8153833f77ca6d1d469071af5db4afb0c` before UI refinement work begins.

The business machine remains in place. This inventory governs the replacement of its visible shell:

- layout;
- typography;
- color usage;
- spacing;
- borders, radii and elevation;
- buttons, fields, statuses and notices;
- route-level visual structure;
- operator-visible terminology;
- interaction feedback and responsive presentation.

The machine-readable authority is `webapp/design/ui-visual-inventory.v1.json`.

## Plain-language conclusion

The existing frontend does not need a second application. Its functional wiring is valuable, but much of its visual implementation can be removed or rewritten.

The correct treatment is:

1. keep business logic, API binding, authorization, drafts, confirmations and state truth;
2. keep one token authority and one shared component directory;
3. rewrite the visible CSS in place;
4. introduce only the missing semantic visual responsibilities;
5. migrate all consumers;
6. delete the old selectors, patch layers and visual expressions.

## Current style surface

There are 11 active source stylesheets.

### Keep as authority

- `webapp/src/styles/tokens.css` — the only semantic design-token authority;
- `webapp/src/a11y.css` — cross-route focus and reduced-motion behavior, with obsolete selectors removed as needed.

These files are not protected from improvement. Their ownership is protected. Token values and focus presentation can be professionally refined without introducing another namespace.

### Rewrite in place

- `webapp/src/styles.css`;
- `webapp/src/styles/components.css`;
- `webapp/src/styles/auth.css`;
- `webapp/src/app/app-shell.css`;
- `webapp/src/features/operator-workspace/operator-workspace.css`;
- `webapp/src/features/admin-routes/admin-routes.css`;
- `webapp/src/features/knowledge/knowledge.css`;
- `webapp/src/features/runtime/runtime-evidence-audit.css`.

The file paths may remain because they are existing ownership boundaries. Their current visual content is not the target design.

### Fold and delete

- `webapp/src/features/operator-workspace/operator-workspace-refinements.css`.

This is a patch layer over the primary Workspace stylesheet. Every still-valid rule must move into the canonical stylesheet or a shared primitive. The file and import must then be deleted.

## Root visual problems

### 1. One pill is doing too many jobs

`Badge` currently presents:

- source;
- priority;
- owner;
- SLA;
- refresh;
- counts;
- task totals;
- runtime state;
- action outcome.

These are not the same kind of information. The result is a screen full of bubbles with no stable hierarchy.

Root correction:

- Badge for compact metadata;
- StatusIndicator for operational state;
- Count for totals;
- Notice for warning, degraded, success and failure feedback.

### 2. Cards and borders are the default structure

Workspace, Knowledge, Channels, Runtime, Control Tower and Runtime Audit repeatedly use:

`white surface + border + radius + another bordered object inside`.

The result is fragmented and visually generic.

Root correction:

- continuous work surfaces;
- sections and dividers;
- list rows;
- toolbars;
- progressive disclosure;
- true elevation only for dialogs and floating surfaces.

### 3. Shared empty/error/detail states are incomplete

`EmptyState` and `ErrorSummary` output shared class names but the active shared stylesheets do not define complete visual treatments for them. `TechnicalDetails` depends partly on route-private styling.

Root correction:

Complete these shared primitives before route-level redesign so every page receives one consistent loading, empty, degraded, warning and failure language.

### 4. Workspace JSX and CSS have drifted apart

Current JSX renders active names including:

- `operator-evidence-panel`;
- `operator-evidence-list`;
- `operator-conversation-panel`.

The main stylesheet still targets older names including:

- `operator-evidence`;
- `operator-conversation`.

It also retains styling for `operator-app-header` and `operator-scope`, although AppShell now owns those responsibilities.

This is a direct reason the interface looks unfinished: some current structures do not receive the intended styling, while deleted structures still occupy CSS.

Root correction:

Rebuild the Workspace stylesheet against the current JSX contract and delete every dead selector.

### 5. The product signature is missing visually

The approved design authority defines the Case Spine:

`Scope -> Evidence -> Decision -> Action -> Operational result -> Customer notification -> Closure / observation`

The current page uses headings, panels, badges and explanatory text instead of this dominant functional structure.

Root correction:

Render the Case Spine only from durable or server-provided facts. When facts are missing, display an explicit incomplete/unavailable state rather than inferring progress.

### 6. Visual state projection is distributed

Labels and tones are produced through:

- `domain/operationalPresentation.ts`;
- `lib/supportStatus.ts`;
- `lib/operatorWorkspacePresentation.ts`.

This is acceptable only when these remain domain mapping helpers. They must all render through the same shared visual components rather than inventing route-specific shapes.

## Shared component disposition

The current shared component directory remains the only authority:

`webapp/src/components/ui/`

Existing components are not duplicated. Their functionality is retained and their visual implementation is rewritten:

- Button;
- ButtonLink;
- Badge;
- Field/Input/Select/Textarea;
- EmptyState;
- ErrorSummary;
- TechnicalDetails;
- ConfirmDialog;
- PageHeader.

Four semantic responsibilities are currently missing and may be added to the same directory only after their replacement scope is explicit:

- StatusIndicator;
- Count;
- Notice;
- LoadingState.

These are not a second component system. They split responsibilities that are currently incorrectly forced into Badge or route-private CSS.

## Route treatment

### `/login`

Preserve authentication behavior and error focus. Replace the generic two-panel framed card presentation with a direct operator login surface.

### `/workspace`

Preserve queue reads, selection, deep links, drafts, refresh, permissions, confirmations and mutations. Replace the visual structure with:

- compact continuous queue;
- dominant case surface;
- visible owner, urgency and blocker;
- truthful Case Spine;
- one next action;
- calmer evidence and conversation flow;
- technical detail behind disclosure.

### `/knowledge`

Preserve search, edit, draft guard, publish and retrieval testing. Keep the useful list/editor/verification model while removing repeated panel chrome.

### `/channels`

Preserve account health and onboarding/repair task behavior. Replace form-card grids with channel health and work-queue structures.

### `/runtime`

Preserve runtime evidence and audit behavior. Establish a clear ready/degraded/failed hierarchy and keep technical content progressively disclosed.

### `/control-tower`

Preserve management evidence and canonical drill-down. Replace generic KPI cards with actionable risk and workload lists.

## External framework boundary

No external visual framework is selected by this inventory.

The decision comes after the current visual surface is accepted because the replacement requirement must be known first.

An external project may be used in one of two ways:

1. **donor** — absorb specific interaction or visual patterns into the existing Nexus component authority;
2. **full replacement authority** — migrate all consumers and delete the replaced visual system in the same delivery path.

It may not be layered beside the current system indefinitely.

## Implementation order

1. Accept and machine-check this inventory.
2. Complete shared Empty, Error, Loading, Notice, Status and Count presentation.
3. Rewrite shared buttons, fields, dialog and header appearance.
4. Rewrite AppShell and login visuals.
5. Rewrite Workspace visual structure and delete the refinement patch layer.
6. Apply the same vocabulary to supporting routes.
7. Delete old selectors, compatibility classes and unused styles.
8. Run local architecture, lint, typecheck, tests, build and browser evidence on one unchanged Head.

## Completion condition

The work is complete only when:

- the functional machine still behaves the same;
- one visual system remains;
- old visual selectors and patch layers are physically removed;
- no old/new switch exists;
- no parallel framework remains;
- every active page uses the same shared visual language;
- browser evidence proves the normal and failure states at the required viewports.
