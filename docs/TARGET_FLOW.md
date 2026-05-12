# NexusDesk P0 Target Flow

This branch closes the P0 business flow only. It does not upgrade OpenClaw, does not add real SMTP/SES/SendGrid, and does not implement inbound email webhooks.

## Target

```text
Webchat intake
→ Ticket
→ AI email draft
→ Human approve
→ Sandbox EmailProvider dispatch
→ Timeline audit
```

WhatsApp remains routed through the existing OpenClaw path. Email is owned by NexusDesk and must not be sent through OpenClaw.

## Channel policy

Customer outbound is limited to:

- `email`
- `whatsapp`

Inbound-only or blocked customer outbound channels:

- `web_chat`
- `telegram`
- `sms`
- `internal`

## Webchat policy

Webchat is intake-only. It may create/update tickets and write visitor messages, comments, and audit events. It must not create customer-visible Webchat agent/system/AI replies.

## AI policy

AI may create draft outbound only. AI-generated outbound is saved as `draft` with `provider_status=ai_review_required`. It is not eligible for dispatch until a human approves it.
