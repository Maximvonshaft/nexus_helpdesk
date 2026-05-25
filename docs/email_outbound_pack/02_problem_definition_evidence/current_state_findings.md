# Current State Findings

## Finding F-001 — Email exists as enum but is blocked

`SourceChannel.email` exists, but the capability registry classifies Email as experimental and not customer-sendable.

Impact:
- UI/API can know the concept of email.
- Production send is blocked by backend gate.
- This is correct for safety but incomplete for business operations.

## Finding F-002 — Send endpoint already has proper guard location

`POST /api/tickets/{ticket_id}/outbound/send` calls `require_outbound_channel_sendable` before queueing the outbound message.

Impact:
- Email can be enabled by evolving the registry safely.
- Do not bypass this gate.

## Finding F-003 — Existing outbox model is reusable

`TicketOutboundMessage` already tracks channel, status, provider status, retries, provider message id, failure code/reason, locks, and timestamps.

Impact:
- Reuse it as the canonical outbound queue.
- Add email-specific metadata tables instead of replacing outbox.

## Finding F-004 — WhatsApp adapter provides pattern

`outbound_adapters/whatsapp.py` resolves account/target and calls provider bridge.

Impact:
- Implement `outbound_adapters/email.py` with same shape:
  - route resolution,
  - account validation,
  - provider dispatch,
  - structured route context.

## Finding F-005 — Current provider allowlist is OpenClaw-only

`message_dispatch.py` only allows `OUTBOUND_PROVIDER=openclaw`.

Impact:
- Email should not be forced into OpenClaw.
- Add email-specific enable flag/provider config without breaking existing OpenClaw channels.

## Finding F-006 — ChannelAccount is too generic for email

Existing `ChannelAccount` has provider/account/market/health/fallback, but no email-specific From/Reply-To/Return-Path/identity verification fields.

Impact:
- Keep ChannelAccount as generic entry.
- Add linked `EmailChannelAccount` table.

## Finding F-007 — Email production requires inbound and delivery events

Outbound-only email is not enough for customer support because customer replies and bounces must return to NexusDesk.

Impact:
- Implement delivery event ingestion.
- Implement inbound reply parser/linker.
