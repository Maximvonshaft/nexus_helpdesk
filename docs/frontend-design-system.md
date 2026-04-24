# Frontend Design System Notes

Core components should remain consistent across Overview, Workspace, Runtime, Accounts, Users, Bulletins, and AI Control.

## Required states

- loading
- empty
- error
- permission denied
- blocked by safety gate
- review required
- successful save

## Workspace safety language

Separate these actions clearly:

1. Save internal handling result.
2. Save customer-facing draft.
3. Send to customer after safety review.

No page should introduce a dangerous direct-send shortcut without backend safety gate coverage.
