# Email Admin API Contract v1.2

## Admin account APIs

All endpoints require channel-account management permission or admin/manager role.

### List Email accounts

```http
GET /api/admin/email/channel-accounts
```

Response item:

```json
{
  "id": 1,
  "channel_account_id": 10,
  "provider": "ses",
  "display_name": "CH Support Email",
  "market_id": 1,
  "from_email": "support.ch@example.com",
  "from_name": "Speedaf Support",
  "reply_to_email": "support.ch@example.com",
  "return_path_email": "bounce.ch@example.com",
  "region": "eu-west-1",
  "configuration_set": "nexusdesk-ch-support",
  "secret_ref": "ses_ch_support",
  "identity_status": "verified",
  "is_verified": true,
  "health_status": "healthy",
  "is_active": true,
  "priority": 100,
  "fallback_account_id": null,
  "readiness": {
    "ready": true,
    "missing": []
  }
}
```

### Create Email account

```http
POST /api/admin/email/channel-accounts
```

Request:

```json
{
  "display_name": "CH Support Email",
  "market_id": 1,
  "from_email": "support.ch@example.com",
  "from_name": "Speedaf Support",
  "reply_to_email": "support.ch@example.com",
  "return_path_email": "bounce.ch@example.com",
  "region": "eu-west-1",
  "configuration_set": "nexusdesk-ch-support",
  "secret_ref": "ses_ch_support",
  "priority": 100,
  "fallback_account_id": null,
  "is_active": true
}
```

Backend behavior:

- Create `ChannelAccount(provider='email')` as routing anchor.
- Create `EmailChannelAccount` companion record in same transaction.
- Validate email fields and header injection.
- Do not store raw credentials.

### Update Email account

```http
PATCH /api/admin/email/channel-accounts/{id}
```

Same validation as create. Provider remains SES in V1.

### Check verification

```http
POST /api/admin/email/channel-accounts/{id}/check-verification
```

Backend calls provider or checks configured provider evidence. It updates `identity_status`, `is_verified`, `last_health_check_at`, and readiness details.

### Health check

```http
POST /api/admin/email/channel-accounts/{id}/health-check
```

Must verify:

- Secret ref resolves.
- SES client can be constructed.
- Sending identity status is acceptable.
- Configuration set exists or is optional and empty.
- Account is not suppressed globally.

### Test send

```http
POST /api/admin/email/channel-accounts/{id}/test-send
```

Request:

```json
{
  "to_email": "internal-test@example.com"
}
```

Rules:

- In production, test recipient must match allowlisted domains unless explicitly disabled by config.
- Test send must create an auditable record or admin audit log.
- Test send must not create a customer ticket.

## Agent send API extension

Existing endpoint remains:

```http
POST /api/tickets/{ticket_id}/outbound/send
```

Backward-compatible request:

```json
{
  "channel": "email",
  "body": "Hello customer...",
  "subject": "Re: [CS-...] Delivery update",
  "to_email": "customer@example.com",
  "cc": [],
  "bcc": [],
  "html_body": null,
  "attachment_ids": []
}
```

Non-email channels must continue accepting only `{channel, body}`.

## Integration APIs

```http
POST /api/integrations/email/ses/events
POST /api/integrations/email/ses/inbound
```

Both require concrete verification:

- SNS signature verification, or
- HMAC timestamp anti-replay.

Unsigned payloads must be rejected.
