# Integration Contract

## Provider abstraction

Create:

```text
backend/app/services/email_providers/base.py
backend/app/services/email_providers/ses.py
```

### Base contract

```python
@dataclass
class EmailSendInput:
    from_email: str
    from_name: str | None
    to_email: str
    cc: list[str]
    bcc: list[str]
    reply_to_email: str | None
    return_path_email: str | None
    subject: str
    text_body: str
    html_body: str | None
    headers: dict[str, str]
    idempotency_key: str
    configuration_set: str | None

@dataclass
class EmailSendResult:
    status: MessageStatus
    provider_status: str
    provider_message_id: str | None
    sent_at: datetime | None
    raw_response: dict
```

## SES outbound

Provider input:
- region
- access key secret ref
- source/from email
- destination
- subject/body
- reply-to
- configuration set
- custom headers if using raw email

Provider output:
- MessageId
- request id if available
- status

## Delivery event inbound

Endpoint:

```http
POST /api/integrations/email/events/ses
```

Payload:
- raw provider event body
- signature/header validation
- idempotent event persistence

## Inbound email

Endpoint:

```http
POST /api/integrations/email/inbound/ses
```

Recommended SES setup:
- inbound email stored to S3 or passed through SNS/Lambda bridge
- Nexus receives normalized payload with raw object pointer
- parser extracts from/to/subject/Message-ID/In-Reply-To/References/text/html

## Integration authentication

Use one of:
- HMAC shared secret header,
- cloud-native signed webhook verification,
- mTLS or private network ingress,
- integration client key with scoped permission.

Do not expose unauthenticated webhook endpoints.
