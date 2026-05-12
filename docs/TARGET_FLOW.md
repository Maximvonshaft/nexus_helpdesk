# NexusDesk P0 Target Flow

This branch closes the P0 formal outbound policy while preserving Webchat as the AI frontline service channel. It does not upgrade OpenClaw, does not add real SMTP/SES/SendGrid, and does not implement inbound email webhooks.

## Target

```text
Webchat AI frontline service
→ Ticket
→ Human resolution note
→ AI formal outbound draft
→ Human approve
→ Email/WhatsApp dispatch
→ Timeline audit
```

Webchat is the AI frontline service channel. Email and WhatsApp are the formal resolution notification channels.

## Channel policy

Customer formal outbound is limited to:

- `email`
- `whatsapp`

Formal/final resolution outbound is blocked on:

- `web_chat`
- `telegram`
- `sms`
- `internal`

## Webchat policy

Webchat may provide customer-visible AI frontline service replies, including greeting, acknowledgement, missing tracking-number/contact requests, trusted tracking-fact replies, handoff acknowledgement, and support-team handoff notices.

Webchat must not carry final/formal resolution notifications, compensation decisions, redelivery commitments, refund/claim confirmations, resolved/closed final notices, or outbound generated from a human resolution note. Those messages must be drafted and approved through the Ticket workflow, then sent by Email or WhatsApp.

Runtime defaults:

```text
WEBCHAT_FRONTLINE_AI_ENABLED=true
WEBCHAT_FORMAL_OUTBOUND_ENABLED=false
```

## AI policy

AI may create formal outbound drafts only. AI-generated formal outbound is saved as `draft` with `provider_status=ai_review_required`. It is not eligible for dispatch until a human approves it.

AI frontline Webchat replies are local Webchat service messages and are not external provider dispatch. They remain subject to safety/fact gates and must not represent final human resolution decisions.
