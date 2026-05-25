# E2E Business Acceptance Checklist v1.2

Email outbound is accepted only if all checklist items are complete.

## Admin configuration

- [ ] Admin can open Email account configuration UI.
- [ ] Admin can create Email account metadata without code changes.
- [ ] Admin cannot store raw provider secret in DB.
- [ ] Admin can see verification, health, and readiness status.
- [ ] Admin can run test-send.
- [ ] Missing DNS/SES/secret items are shown in plain language.

## Agent workflow

- [ ] Agent sees Email only when the ticket and account are ready.
- [ ] Agent can see From, To, Subject, Body before sending.
- [ ] Agent cannot force-send when Email is not ready.
- [ ] Email send returns queued state, not fake delivered state.

## Provider and delivery

- [ ] Worker sends Email through SES provider abstraction.
- [ ] Provider message id is persisted.
- [ ] Delivery event is stored.
- [ ] Bounce creates suppression.
- [ ] Complaint creates suppression.

## Inbound reply

- [ ] Plus-address and headers link to correct ticket.
- [ ] Subject-only match does not auto-link.
- [ ] Unresolved inbound messages are visible for review.

## Operations

- [ ] Email-specific queue counts exist.
- [ ] Rollback disables Email without affecting WhatsApp/Telegram/SMS/WebChat.
- [ ] Pending Email rows are not dead-lettered by rollback.
- [ ] Final smoke evidence pack exists.
