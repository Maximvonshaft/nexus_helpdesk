# v1.4 Atomic Issue Template Examples

## Canonical GitHub Issue Format

```markdown
## Atomic Task ID
EMAIL-BE-038

## PR Slice
PR-04 email-dispatch-and-ses-provider

## Owner Role
Backend Engineer

## Depends On
EMAIL-BE-003, EMAIL-BE-034

## Atomic Change
Make outbox claim channel-aware.

## Exact Code-Level Requirement
`claim_pending_messages` must claim `email` only when the Email runtime gate is enabled. WhatsApp/Telegram/SMS remain controlled by OpenClaw gate. Disabled Email pending rows must not be retried or dead-lettered.

## Primary Files
- backend/app/services/message_dispatch.py
- backend/tests/test_email_runtime_gate_and_rollback.py

## Acceptance Criteria
- Email disabled leaves pending Email rows untouched.
- retry_count remains unchanged.
- non-Email channels continue according to existing gates.

## Test Command
```bash
pytest backend/tests/test_email_runtime_gate_and_rollback.py::test_email_disabled_does_not_claim_or_dead_letter_pending_email
```

## Expected Evidence
- Test output
- Before/after DB state proving pending Email row is unchanged

## Rollback Impact
Set `OUTBOUND_EMAIL_ENABLED=false`; no data migration rollback required.

## Merge Independence
Yes

## Priority
P0
```

## Rules

1. One issue should map to one atomic task unless explicitly approved by reviewer.
2. A PR may contain multiple atomic tasks only if they are in the same PR slice and all tests/evidence are included.
3. Do not merge P1/P2 tasks that depend on incomplete P0 tasks.
