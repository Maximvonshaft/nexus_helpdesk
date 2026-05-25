# Solution Overview

## Design principle

Email is implemented as a first-class NexusDesk outbound channel. It reuses the existing outbox lifecycle while adding email-specific account governance, routing, provider delivery events, inbound threading, and suppression.

## Core components

```text
Ticket Reply UI
  -> /api/tickets/{id}/outbound/channels/capabilities
  -> /api/tickets/{id}/outbound/send
  -> TicketOutboundMessage
  -> EmailOutboundMetadata
  -> Worker / message_dispatch.process_outbound_message
  -> outbound_adapters.email.dispatch_email_outbound
  -> email_providers.ses.SESProvider
  -> AWS SES
  -> SES Event Webhook
  -> email_delivery_events + ticket timeline
  -> SES inbound/S3/SNS webhook
  -> email_inbound_messages + ticket comment/timeline
```

## Why not OpenClaw

OpenClaw bridge is appropriate for chat-like channels. Email requires:
- subject and MIME body,
- From/Reply-To/Return-Path,
- Message-ID threading,
- inbound parsing,
- bounce and complaint handling,
- provider-specific event security.

Those concerns are native to Helpdesk, not OpenClaw.

## V1 architecture

- SES API as provider.
- Existing worker/outbox dispatch.
- New email metadata tables.
- Delivery event endpoint.
- Inbound parse/link endpoint.
- Capability-gated UI.

## V1.1 extensions

- Attachments.
- SMTP fallback provider.
- Email templates/signatures.
- Rich admin UI for provider health.
