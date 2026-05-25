# Admin Configuration and Frontend E2E Design v1.2

## Required admin page

Implement either:

- Extend `webapp/src/routes/accounts.tsx` with an Email-specific mode, or
- Add a dedicated `webapp/src/routes/email-accounts.tsx` linked from the existing Channel Accounts page.

Preferred: dedicated Email tab/page to avoid mixing chat-channel routing with Email governance.

## Admin Email Account page sections

### 1. Account list

Columns/cards:

- Display name.
- Market/global scope.
- From email.
- Provider region.
- Identity status.
- Health status.
- Active/inactive.
- Last health check time.
- Pending/sent/bounced counts if available.

### 2. Account editor

Fields:

- Provider: SES only in V1.
- Display name.
- Market.
- From email.
- From name.
- Reply-To.
- Return-Path.
- SES region.
- Configuration set.
- Secret reference.
- Priority.
- Fallback account.
- Active toggle.

### 3. Readiness checklist

Display backend-calculated readiness items:

- Runtime flag enabled.
- Email provider configured.
- Secret reference resolvable.
- From identity verified.
- DKIM status known.
- Configuration set available.
- Inbound enabled/disabled.
- Webhook verification enabled.
- Suppression check enabled.

### 4. Actions

- Save.
- Check verification.
- Health check.
- Send test email.
- Disable account.
- View recent delivery events.
- View suppression entries.

## Required agent reply composer behavior

Update `CustomerReplyPanel` or equivalent ticket reply component:

- Use capability API as source of truth.
- If Email is unavailable, show missing reasons.
- When Email is selected, render:
  - From account, read-only.
  - To email, default from ticket/customer, editable only if user has permission.
  - Subject, prefilled with `Re: [ticket_no] {title}` or previous thread subject.
  - Body editor.
  - CC/BCC collapsed advanced section.
  - External send confirmation checkbox.
- Submit payload with Email-specific fields.
- On success, show queued state, not fake delivered state.

## Timeline UI

Timeline must distinguish:

- Email queued.
- Email accepted by provider.
- Email delivered.
- Email bounced.
- Complaint received.
- Customer email reply received.
- Email blocked due to suppression.

Ticket status must not be confused with Email delivery status.

## Queue/admin observability

Admin queue summary must add Email-specific counts:

- pending_email_outbound.
- processing_email_outbound.
- sent_email_outbound_24h.
- dead_email_outbound.
- delivered_email_events_24h.
- bounced_email_events_24h.
- complaint_email_events_24h.
- suppressed_email_recipients.

## UX acceptance

A non-technical admin must be able to understand why Email is not ready without reading logs.
