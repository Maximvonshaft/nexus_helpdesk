# Release Gate

## Gate 1 — Code Review

- Backend owner approved.
- Security/privacy reviewed.
- Migration reviewed.
- Frontend reviewed if UI changed.

## Gate 2 — CI

- Email tests pass.
- Existing outbound tests pass.
- Frontend checks pass if touched.
- Migration validation passes.

## Gate 3 — Staging

- Capability ready.
- Send smoke passed.
- Delivery event smoke passed.
- Inbound smoke passed.
- Rollback smoke passed.

## Gate 4 — Production enablement

- Release owner approval.
- Support lead approval.
- On-call coverage active.
- Monitoring dashboard ready.
