# P0 Release Gate — Webchat Intake, Email/WhatsApp Outbound

This gate is scoped to the P0 closure branch only.

## Must pass

- Webchat inbound creates/updates tickets.
- Webchat outbound is disabled by backend policy.
- Frontend default Webchat inbox does not expose Webchat reply textarea/button.
- Webchat-created tickets default to `preferred_reply_channel=email`.
- Webchat tickets without customer email do not fall back to WhatsApp.
- Customer outbound channel policy is exactly `email` and `whatsapp`.
- `web_chat`, `telegram`, `sms`, and `internal` are rejected for customer outbound dispatch.
- AI auto reply policy creates draft only.
- Human approval is required before a draft can become pending dispatch.
- Email dispatch uses NexusDesk local `SandboxEmailProvider`.
- Email dispatch does not use OpenClaw.
- WhatsApp dispatch remains on the OpenClaw route.
- Provider success writes provider result into `provider_message_id`.
- Provider failure does not mark outbound as sent.
- Ticket events record draft, approval, dispatch start, sent/failure, retry/dead lifecycle through existing `TicketEvent` rows.

## Must fail if

- Any default Webchat intake flow creates `WebchatMessage(direction=agent)` or `WebchatMessage(direction=system)` as a customer-visible reply.
- Any default Webchat intake flow creates `TicketOutboundMessage(channel=web_chat,status=sent)`.
- Email dispatch touches OpenClaw.
- AI can create pending/sent outbound without human approval.
- Direct `/api/tickets/{ticket_id}/outbound/send` queues pending outbound by default.

## Non-goals

- No OpenClaw upgrade.
- No real SMTP/SES/SendGrid/Mailgun integration.
- No inbound email webhook.
- No full operator UI redesign.
- No Telegram/SMS production outbound enablement.
