# Agentic Design System Foundation

This directory introduces the first behavior-neutral design system foundation for NexusDesk.

## What this PR adds

- primitive component contracts: `Button`, `Badge`, `Card`
- business component contracts: `StatusBadge`, `SafetyGateBadge`
- semantic design tokens in `webapp/src/styles/tokens.css`
- component class contracts in `webapp/src/styles/components.css`
- public shared UI exports through `webapp/src/shared/ui/index.ts`

## Behavior impact

None expected.

The new CSS files are intentionally not imported yet. Existing active styles remain in `webapp/src/styles.css`.

No existing route imports were changed.
No API client behavior was changed.
No auth behavior was changed.
No WebChat widget behavior was changed.

## Migration rule

Future PRs should adopt these components one low-risk surface at a time, with screenshots and smoke evidence.

Recommended next adoption order:

1. low-risk admin/status surfaces
2. runtime badges/cards
3. AI governance config surfaces
4. WebChat admin shell
5. Workspace cockpit components

Do not mass-replace all UI components in one PR.
