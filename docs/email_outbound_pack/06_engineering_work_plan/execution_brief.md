# Engineering Execution Brief

## Task

Implement production-grade Email outbound channel for NexusDesk.

## Repository

`Maximvonshaft/nexus_helpdesk`

## Target branch

`feat/email-outbound-production`

## Risk Level

`R4`

Reason: customer-facing outbound communication, PII, external provider integration, delivery webhooks, inbound reply ingestion.

## Objective

Convert `email` from `experimental_not_ready` into a production-capable, capability-gated, provider-backed support channel.

## Required Outcome

- Email capability becomes sendable only when runtime/account/recipient/provider gates pass.
- Agent can queue Email outbound from a ticket.
- Worker sends Email through SES provider abstraction.
- Provider message id is persisted.
- Delivery/bounce/complaint events are ingested and visible.
- Customer inbound replies are parsed and linked to ticket.
- All new behavior has automated tests.
- Production defaults are fail-closed.
- Rollback is configuration-only.

## Constraints

- Preserve WhatsApp/Telegram/SMS/WebChat behavior.
- Do not bypass `require_outbound_channel_sendable`.
- Do not store provider secrets in DB.
- Do not log raw secrets or full provider auth payloads.
- Do not implement marketing/bulk sending.
- Keep Email disabled by default.
- Use migrations; do not rely on auto-create tables.
- Keep code minimal and reviewable.

## Required Deliverables

- Code changes.
- Alembic migration.
- Unit and integration tests.
- Updated ops docs.
- PR description.
- Final engineer report.
- Staging smoke evidence template/results.

## Suggested PR sequence

1. Data model + settings + capability registry.
2. Email adapter + SES provider + dispatch integration.
3. Delivery event webhook + suppression.
4. Inbound parser/linker.
5. Frontend compose/account UI.
