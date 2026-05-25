# Invariants and Constraints

## Invariants

1. No Email send without `OUTBOUND_EMAIL_ENABLED=true`.
2. No Email send without `ENABLE_OUTBOUND_DISPATCH=true`.
3. No Email send without an active `ChannelAccount(provider=email)`.
4. No Email send without a linked verified `EmailChannelAccount`.
5. No Email send without a valid recipient.
6. No Email send to suppressed recipient.
7. No provider secret may be stored in DB or logs.
8. No duplicate provider send for the same outbound message idempotency key.
9. API thread must not perform long-running provider send; worker handles dispatch.
10. Customer-visible email body must be sanitized before timeline rendering.
11. Bounce/complaint must be visible to operators.
12. Rollback must be possible through env flags.

## Constraints

- Keep changes reviewable; no broad refactor.
- Preserve current outbound behavior for non-email channels.
- Add tests before enabling Email as sendable.
- Production defaults remain fail-closed.
