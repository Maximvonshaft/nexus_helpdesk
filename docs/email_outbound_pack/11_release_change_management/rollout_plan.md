# Rollout Plan

## Phase 0 — Code deployed disabled

- Merge code.
- Deploy with Email disabled.
- Run health checks.
- Confirm Email not sendable.

## Phase 1 — Staging smoke

- Configure staging SES identity.
- Enable Email in staging.
- Send internal smoke.
- Trigger delivery/bounce event.
- Test inbound reply.

## Phase 2 — Production internal smoke

- Deploy to production with Email disabled.
- Configure production SES identity.
- Enable for internal test ticket only if feature supports scope.
- Send internal smoke.
- Disable if any unexpected event occurs.

## Phase 3 — Controlled market rollout

- Enable for selected market/team.
- Monitor for 24–72 hours.
- Review bounce/complaint/inbound unresolved.

## Phase 4 — Broader rollout

- Enable wider account coverage.
- Continue monitoring.
