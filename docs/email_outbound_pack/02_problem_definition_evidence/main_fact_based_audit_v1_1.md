# Main-fact-based Final Audit and v1.1 Optimization

Pack version: `v1.1`  
Audit date: `2026-05-25`  
Repository target: `Maximvonshaft/nexus_helpdesk`  
Baseline branch: `main`

## Executive verdict

The v1 architecture direction is correct and can achieve the Email customer-support outbound business objective, but v1 is not sufficient for a 100% production guarantee unless the guardrails in this v1.1 addendum are implemented.

Final status:

```text
APPROVED_FOR_IMPLEMENTATION_WITH_V1_1_GUARDRAILS
```

The engineer must treat this document as mandatory. If any P0 guardrail is skipped, the implementation must not be merged.

## Current main facts verified

### F-001 Email is present but blocked

`backend/app/services/outbound_channel_registry.py` currently places `email` under `EXTERNAL_EXPERIMENTAL_CHANNELS` and returns `experimental_not_ready` with missing items:

- `email_account_registry`
- `email_send_schema`
- `email_provider_adapter`

This is the correct starting point.

### F-002 Existing outbound endpoint is already capability-gated

`backend/app/api/tickets.py` calls `require_outbound_channel_sendable(...)` before `send_outbound_message(...)` on:

```http
POST /api/tickets/{ticket_id}/outbound/send
```

This endpoint can be reused if Email-specific fields are added backward-compatibly.

### F-003 Existing outbox worker can be reused but must become channel-aware

`backend/app/services/message_dispatch.py` currently has queue, claim, retry, sent, dead-letter, and requeue logic.

However, the global runtime gate currently assumes external dispatch is controlled by:

```text
ENABLE_OUTBOUND_DISPATCH
OUTBOUND_PROVIDER=openclaw
```

Email must not overload `OUTBOUND_PROVIDER`.

### F-004 Email is already classified as external semantics

`backend/app/services/outbound_semantics.py` includes `email` in `EXTERNAL_OUTBOUND_CHANNELS`. This is correct for UI/timeline semantics, but worker claim eligibility must not blindly use this set.

### F-005 ChannelAccount resolver has a provider-scope hazard

`resolve_channel_account(...)` in `backend/app/services/openclaw_bridge.py` currently resolves any active `ChannelAccount` by market/global fallback without provider scoping.

If `provider=email` rows are added without provider-scoped resolution, WhatsApp/Telegram/SMS generic paths may accidentally pick an Email account.

### F-006 Admin channel account provider validation is OpenClaw-oriented

`backend/app/api/admin.py` validates channel account provider through `ALLOWED_CHANNEL_ACCOUNT_PROVIDERS`, currently defined in `openclaw_bridge.py` as:

```python
{'whatsapp', 'telegram', 'sms'}
```

Email account governance must not simply add `email` to an OpenClaw provider constant without separating provider concerns.

### F-007 Current frontend send payload is body-only

`webapp/src/lib/api.ts` currently defines:

```ts
type OutboundSendPayload = { channel: string; body: string }
```

Email requires backward-compatible optional fields such as subject, to_email, cc, bcc, html_body, and attachment_ids.

### F-008 boto3 is already available

`backend/requirements.txt` already includes `boto3`, so SES provider implementation does not require a new heavyweight dependency.

## Business objective

The optimized implementation must deliver this business closure:

```text
Support agent can reply to a customer from a ticket by Email.
Email appears only when ready.
SES sends from worker, not request thread.
Provider message id is persisted.
Delivery/bounce/complaint events are recorded.
Customer replies can return to the original ticket.
Rollback disables Email without damaging pending Email queue.
No Email account can contaminate non-Email outbound routes.
```

## v1 risk assessment

| Risk | v1 state | v1.1 required action |
|---|---|---|
| Email account contaminates OpenClaw channel resolution | Not fully controlled | Add provider-scoped resolver and tests |
| Email disable marks pending messages dead | Not fully controlled | Add channel-aware worker eligibility and email pause semantics |
| `OUTBOUND_PROVIDER=ses` confusion | Not fully controlled | Add Email-specific provider gate; keep `OUTBOUND_PROVIDER=openclaw` for non-email |
| Email schema breaks existing channels | Partially controlled | Extend schema backward-compatibly |
| Inbound reply auto-link false positives | Partially controlled | Forbid subject-similarity auto-link in V1 |
| SES inbound region/DNS drift | Partially controlled | Add preflight gate |
| Webhook authentication too generic | Partially controlled | Require concrete SNS signature/HMAC implementation |
| Queue/admin observability not channel-specific | Partially controlled | Add Email queue breakdown |

## Final merge condition

No PR may be marked production-ready unless all P0 and P1 requirements in:

```text
03_scope_contract/p0_p1_guardrails_addendum.md
```

are implemented and tested.
