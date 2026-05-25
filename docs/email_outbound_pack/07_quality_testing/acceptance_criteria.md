# Acceptance Criteria

## AC-001 — Email blocked by default

Given production defaults are loaded
When `/api/tickets/{id}/outbound/channels/capabilities` is called
Then Email is not sendable
And `missing[]` includes dispatch/email provider configuration reasons.

## AC-002 — Email capability ready only when fully configured

Given a ticket has a valid customer email
And an active verified Email Channel Account exists
And `ENABLE_OUTBOUND_DISPATCH=true`
And `OUTBOUND_EMAIL_ENABLED=true`
And `EMAIL_PROVIDER=ses`
When capability API is called
Then Email returns `customer_sendable=true`
And `supports_send=true`
And `status=ready`.

## AC-003 — Agent can queue Email

Given Email capability is ready
When an authorized agent posts `/api/tickets/{id}/outbound/send` with `channel=email`
Then a `TicketOutboundMessage` row is created with `status=pending`
And linked `EmailOutboundMetadata` is created
And a ticket event/timeline entry records queued email.

## AC-004 — Worker sends through SES provider

Given a pending Email outbound message
When worker dispatch runs
Then Email adapter resolves route/account/recipient
And SES provider is called once
And provider message id is persisted
And outbound status becomes `sent`
And ticket conversation state becomes `waiting_customer`.

## AC-005 — Delivery events are captured

Given SES sends a delivery/bounce/complaint event
When webhook endpoint receives a valid event
Then `email_delivery_events` stores it idempotently
And ticket timeline shows the event
And bounce/complaint updates suppression state when applicable.

## AC-006 — Inbound customer reply links to ticket

Given customer replies to a Nexus email
When inbound parser receives the raw/normalized email
Then it links to the original ticket using header or plus-address
And creates an inbound email record
And adds customer-visible timeline/comment data.

## AC-007 — Invalid recipient blocks send

Given a ticket has no valid customer email
When agent attempts Email send
Then API returns 400 with `outbound_channel_not_ready`
And no outbound message is queued.

## AC-008 — Suppressed recipient blocks send

Given an email is suppressed due to bounce/complaint
When agent attempts Email send
Then API returns 400 with a suppression missing/reason code
And no outbound message is queued.

## AC-009 — Header injection blocked

Given subject/from/to/reply-to contains CR/LF injection
When Email send is validated
Then request is rejected
And no provider call occurs.

## AC-010 — Rollback disables Email

Given Email was previously configured
When `OUTBOUND_EMAIL_ENABLED=false`
Then Email is no longer sendable
And worker does not dispatch Email provider sends.

## AC-011 — Existing channels unaffected

Given WhatsApp/WebChat/SMS/Telegram existing flows
When Email implementation is merged
Then existing tests continue to pass
And non-email outbound semantics do not change.

## AC-012 — UI is not misleading

Given Email is not ready
When agent opens reply composer
Then Email is hidden or disabled with exact reason
And no fake send button is shown.

## v1.1 hard acceptance criteria

The implementation is not accepted unless all items below pass.

### Channel/account isolation

- Creating `ChannelAccount(provider="email")` does not affect WhatsApp/Telegram/SMS account resolution.
- Non-email OpenClaw routes only resolve accounts of the same provider.
- Email adapter only resolves `provider=email`.

### Runtime gate and rollback

- `OUTBOUND_PROVIDER` remains `openclaw` for non-email channels.
- Email uses `OUTBOUND_EMAIL_ENABLED` + `EMAIL_PROVIDER=ses`.
- Email disabled does not claim pending Email messages.
- Already processing Email disabled mid-flight does not increment retry count and does not mark dead.

### API compatibility

- Existing `{channel, body}` payloads for WhatsApp/Telegram/SMS/WebChat remain valid.
- Email payload supports optional subject/to_email/html_body fields.
- Email rejects missing/invalid recipient before queueing.

### Inbound safety

- Subject similarity cannot auto-link inbound Email.
- Deterministic ticket linking works via plus-address/header/message references.
- Ambiguous inbound messages go to unresolved/manual review.

### Webhook security

- Unsigned or stale webhooks are rejected.
- Duplicate provider events are idempotent.
- Bounce/complaint creates suppression.

### Observability

- Admin queue summary exposes Email counts separately from generic outbound counts.
- Timeline/event records show queued/sent/delivered/bounced/complained without leaking secrets.


## v1.2 E2E Admin + Agent Acceptance Criteria

Email outbound is not accepted unless:

1. Admin Email account configuration UI exists and is wired to real backend APIs.
2. Admin can create/update Email account metadata from UI.
3. Admin can run verification check, health check, and test send from UI.
4. Agent ticket reply panel renders Email-specific From/To/Subject/Body fields.
5. Agent cannot send Email when capability reports missing configuration.
6. Existing non-email channels continue to use backward-compatible `{channel, body}` payloads.
7. Email queue and event status are visible in admin observability.
8. Rollback hides/disables Email in UI and does not dead-letter pending Email rows.
