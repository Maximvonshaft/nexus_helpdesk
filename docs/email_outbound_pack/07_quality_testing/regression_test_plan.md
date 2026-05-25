# Regression Test Plan

Must rerun existing outbound tests to avoid breaking:
- WhatsApp adapter.
- Production dispatch gates.
- Outbound message semantics.
- WebChat local-only semantics.
- Ticket send endpoint.
- Auth/RBAC for send.

Regression risk:
- Email could accidentally be included in external channel list and routed to OpenClaw.
- Generic dispatch branch could send email incorrectly.
- Capability UI may show Email as ready without full config.
