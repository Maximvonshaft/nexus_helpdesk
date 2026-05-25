# E2E Critical Paths

## E2E-001 — Successful Email reply

1. Create customer with valid email.
2. Configure active verified email account.
3. Enable staging flags.
4. Send email from ticket.
5. Run worker.
6. Confirm provider message id.
7. Confirm timeline sent entry.
8. Confirm mailbox received message.

## E2E-002 — Bounce

1. Send to provider-supported bounce test address.
2. Receive bounce event.
3. Confirm `email_delivery_events`.
4. Confirm suppression entry.
5. Confirm future send is blocked.

## E2E-003 — Inbound reply

1. Send email with ticket-linked headers/plus address.
2. Reply from customer mailbox.
3. Trigger inbound parser.
4. Confirm reply linked to original ticket.

## E2E-004 — Rollback

1. Enable Email in staging.
2. Confirm capability ready.
3. Set `OUTBOUND_EMAIL_ENABLED=false`.
4. Restart relevant service.
5. Confirm capability not sendable.
6. Confirm worker does not send pending email.
