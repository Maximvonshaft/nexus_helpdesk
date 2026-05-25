# API Contract Guardrails

## Existing API to preserve

```http
POST /api/tickets/{ticket_id}/outbound/send
```

Existing callers using:

```json
{
  "channel": "whatsapp",
  "body": "..."
}
```

must continue to work.

## Required extension

Add optional email fields to `OutboundSendRequest`:

```json
{
  "channel": "email",
  "body": "plain text body",
  "subject": "Optional subject",
  "to_email": "customer@example.com",
  "cc": [],
  "bcc": [],
  "html_body": "<p>optional sanitized html</p>",
  "attachment_ids": []
}
```

## Validation rules

When `channel != email`:
- Ignore or reject email-only fields consistently. Recommended: reject email-only fields with `email_fields_not_allowed_for_channel` to avoid accidental caller bugs.

When `channel == email`:
- `to_email` may be omitted only if resolvable from `ticket.preferred_reply_contact` or `ticket.customer.email`.
- `subject` may be omitted only if resolvable from an existing email thread or generated from ticket.
- Body must be non-empty after trimming.
- `cc` and `bcc` must be optional and empty by default.
- Header values must reject CR/LF injection.
- Recipient must not be suppressed.

## Capability API

Preserve:

```http
GET /api/tickets/{ticket_id}/outbound/channels/capabilities
```

Email capability must expose:
- `customer_sendable`
- `supports_send`
- `enabled`
- `configured`
- `missing`
- `operator_note`
- `target_validation`
- `external_send`

## New integration APIs

```http
POST /api/integrations/email/events/ses
POST /api/integrations/email/inbound/ses
```

Guardrails:
- Must verify provider signature/token where applicable.
- Must be idempotent.
- Must store raw payload for audit, with PII-safe access boundaries.
- Must return 2xx only after durable persistence.
