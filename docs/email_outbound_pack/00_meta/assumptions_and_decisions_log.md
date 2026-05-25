# Assumptions and Decisions Log

| ID | Decision / Assumption | Reason | Reversible? |
|---|---|---|---|
| AD-001 | Email will be implemented as a first-class adapter, not routed through OpenClaw. | Email has protocol-specific requirements: MIME, threading, bounce, complaint, Return-Path, inbound reply parsing. | Yes, but not recommended. |
| AD-002 | AWS SES API is the primary V1 provider. | Low cost, transactional focus, API message ids, event publishing, inbound options. | Yes, provider abstraction allows replacement. |
| AD-003 | Existing `TicketOutboundMessage` remains the outbox row. | Least invasive; current worker/retry/dead-letter logic can be reused. | Yes. |
| AD-004 | Email-specific metadata is stored in linked tables. | Avoid polluting generic outbound row with email-only fields. | Yes. |
| AD-005 | Email remains fail-closed by default. | Prevent accidental customer emails during deployment. | Yes. |
| AD-006 | V1 supports one primary customer recipient by default. | Customer support reply workflow, not mass sending. | Yes. |
| AD-007 | Inbound replies are part of production readiness. | A support email channel is not closed if customer replies disappear outside NexusDesk. | No for production definition. |
| AD-008 | Open/click tracking is disabled by default. | Privacy and compliance risk; not needed for support operations. | Yes. |
