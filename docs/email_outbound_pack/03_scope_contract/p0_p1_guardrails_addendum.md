# P0/P1 Guardrails Addendum for Email Outbound v1.1

This document is mandatory. It supersedes any ambiguous instruction in v1.

## P0-001 Provider-scoped ChannelAccount resolution

### Problem

Current generic channel account resolution may return any active account by market/global fallback. Adding `provider=email` without scoping introduces cross-channel contamination risk.

### Required implementation

Create a provider-scoped resolver, for example:

```python
def resolve_channel_account_for_provider(
    db: Session,
    *,
    provider: str,
    market_id: int | None,
    account_id: str | None = None,
) -> ChannelAccount | None:
    ...
```

Rules:

1. Filter by `ChannelAccount.provider == provider`.
2. Filter by `is_active=True`.
3. If `account_id` is provided, it must also match the provider.
4. Market-specific account has priority over global fallback.
5. Global fallback must also match the provider.
6. Existing non-email routes must not be able to select `provider=email`.

### Required tests

- WhatsApp/Telegram/SMS resolver never returns `provider=email`.
- Email resolver never returns `provider=whatsapp/telegram/sms`.
- Market-specific Email account wins over global Email account.
- Unknown provider returns no account.
- Inactive account is ignored.

## P0-002 Separate Email runtime gate from `OUTBOUND_PROVIDER`

### Problem

Current external dispatch runtime gate assumes `OUTBOUND_PROVIDER=openclaw`. Email must use `EMAIL_PROVIDER=ses` without changing the global OpenClaw provider switch.

### Required implementation

Keep:

```text
OUTBOUND_PROVIDER=openclaw
```

for WhatsApp/Telegram/SMS.

Add:

```text
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
```

for Email.

Implement channel-aware runtime gating:

```text
if channel == email:
    require ENABLE_OUTBOUND_DISPATCH=true
    require OUTBOUND_EMAIL_ENABLED=true
    require EMAIL_PROVIDER=ses
else:
    require ENABLE_OUTBOUND_DISPATCH=true
    require OUTBOUND_PROVIDER=openclaw
```

### Forbidden

- Do not set `OUTBOUND_PROVIDER=ses`.
- Do not add `ses` to `ALLOWED_OUTBOUND_PROVIDERS`.
- Do not route Email through OpenClaw bridge/MCP/CLI.

## P0-003 Email rollback must pause pending Email, not dead-letter it

### Problem

Email-only rollback must not burn the pending queue.

### Required implementation

When Email is disabled:

```text
OUTBOUND_EMAIL_ENABLED=false
EMAIL_PROVIDER=disabled
```

the worker must not claim Email pending rows. If an Email row is already claimed/processing when disable occurs, it must be safely returned to pending/paused state without incrementing `retry_count`.

Required behavior:

| State | Expected behavior |
|---|---|
| pending email + disabled | remains pending; not claimed |
| processing email + disabled | returned to pending/paused; retry_count unchanged |
| sent email + disabled | unchanged |
| dead email + disabled | unchanged |
| non-email channels | unaffected if global outbound remains enabled |

Allowed provider_status value:

```text
email_dispatch_paused
```

Do not use `dead:*` for feature-flag rollback.

### Required tests

- Disable Email and run worker once: pending Email count unchanged.
- Disable Email while non-email is enabled: WhatsApp still dispatchable.
- Already processing Email is reset safely without retry increment.

## P0-004 Backward-compatible Email send schema

### Problem

Current API and webapp send payload only support `channel` and `body`. Email requires more fields.

### Required implementation

Extend `OutboundSendRequest` backward-compatibly:

```python
class OutboundSendRequest(BaseModel):
    channel: SourceChannel
    body: str
    subject: str | None = None
    to_email: EmailStr | None = None
    cc: list[EmailStr] = Field(default_factory=list)
    bcc: list[EmailStr] = Field(default_factory=list)
    html_body: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)
```

Rules:

1. Non-email channels must preserve existing behavior.
2. Email channel must run Email-specific validation.
3. If `to_email` is absent, resolve from `ticket.preferred_reply_contact` then `customer.email`.
4. If no valid recipient exists, return structured HTTP 400 before queueing.
5. `EMAIL_MAX_RECIPIENTS=1` in V1. Reject cc/bcc unless explicitly enabled later.

## P0-005 Webhook authentication must be concrete

### Required implementation

If using AWS SNS directly:

1. Verify SNS message signature.
2. Allow only AWS SNS signing certificate hosts.
3. Reject unsigned payloads.
4. Deduplicate by event id or hash.

If using a controlled gateway in front of NexusDesk:

1. Require HMAC header using `EMAIL_WEBHOOK_SECRET`.
2. Verify timestamp to prevent replay.
3. Reject stale timestamps.

The final code must not rely only on obscurity or unverified `EMAIL_WEBHOOK_SECRET` text in the payload.

## P0-006 No automatic V1 subject-similarity linking

### Problem

Subject similarity can attach a customer email to the wrong ticket.

### Required implementation

Inbound V1 auto-link may only use deterministic identifiers:

1. plus-address ticket id, e.g. `support+ticket-123@domain`
2. `X-NexusDesk-Ticket-ID`
3. `In-Reply-To` / `References` mapping to known `email_outbound_metadata.message_id_header`
4. provider metadata mapping

Subject similarity can only create an unresolved inbound event for manual review.

## P1-001 SES inbound region/DNS preflight

Before enabling inbound:

1. Confirm SES receiving support for selected region.
2. Confirm MX records point to the selected inbound region.
3. Confirm domain identity is verified.
4. Confirm DKIM is active.
5. Confirm SPF/DMARC baseline is configured.
6. Confirm SES event path reaches NexusDesk integration endpoint.

## P1-002 Email queue/admin observability

Add admin/queue breakdown for:

- pending_email_outbound
- processing_email_outbound
- sent_email_outbound
- dead_email_outbound
- paused_email_outbound
- email_bounce_events
- email_complaint_events
- email_inbound_unresolved

## P1-003 Suppression enforcement

Before sending, Email adapter must check suppression entries.

Bounce/complaint events must create or update suppression rows.

Suppressed recipients must not be queued/sent. Return structured error before queueing when detected.

## P1-004 Provider id persistence

Provider message id must be stored in both:

1. `TicketOutboundMessage.provider_message_id` where compatible.
2. `email_outbound_metadata.provider_message_id`.

Local idempotency key may be stored before provider dispatch, but provider id must replace or supplement it after successful SES response.

## P1-005 HTML and header safety

Implement and test:

- CRLF rejection in subject/from/to/reply-to/return-path.
- HTML sanitization or plain-text-only V1.
- No script/style/event attributes.
- No secret values in logs.
- No raw full email body in structured operational logs.

## Implementation order

1. Provider-scoped account resolver and tests.
2. Email-specific settings and runtime gate.
3. Email schema and capability gate.
4. Data models/migration.
5. Email adapter + SES provider.
6. Delivery event webhook.
7. Inbound parser.
8. Frontend and admin UI.
9. Smoke pack and rollout.
