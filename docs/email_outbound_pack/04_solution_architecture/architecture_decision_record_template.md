# ADR — Email Outbound Production Channel

## Status
Accepted for implementation.

## Context
Email is currently represented in the domain model but blocked from sending. Production support requires an auditable email reply path, delivery status, and inbound reply linking.

## Decision
Implement Email as a first-class outbound adapter using AWS SES API as V1 provider. Reuse `TicketOutboundMessage` for queue lifecycle and add email-specific metadata/event/inbound/suppression tables.

## Consequences
Positive:
- Clear production boundary.
- Minimal disruption to existing outbound channels.
- Provider events become auditable.
- Inbound replies can close the channel loop.

Negative:
- Requires new migrations and integration endpoints.
- Requires DNS/domain/provider setup.
- Requires security/privacy review for email PII.

## Alternatives considered

1. Route Email through OpenClaw.
   - Rejected: hides email-specific provider semantics.

2. Use SMTP-only.
   - Rejected as primary path: poorer observability and event handling.

3. Build mailbox-only manual integration.
   - Rejected: does not close workflow inside NexusDesk.
