# UI Layout Remediation Spec

## Ticket detail reply composer

### Channel dropdown

- Use capability API as source of truth.
- Hide or disable Email based on `supports_send`.
- If disabled, show missing reasons.

### Email compose mode

Fields:
- From: read-only from account.
- To: editable only if permission allows; default from customer email.
- Subject: editable, prefilled.
- Body: existing editor.
- CC/BCC: collapsed advanced controls.
- Attachments: disabled in V1 unless backend supports it.

### Timeline

Add timeline cards for:
- Email queued
- Email accepted by provider
- Email delivered
- Email bounced
- Complaint received
- Customer email reply received

### Admin account UI

If extending account UI:
- Provider selector: SES only in V1.
- From email.
- From name.
- Reply-To.
- Return-Path.
- Market.
- Verification status.
- Health check status.
- Test send action behind admin permission.
