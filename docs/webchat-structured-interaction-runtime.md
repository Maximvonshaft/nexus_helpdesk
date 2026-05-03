# WebChat Structured Interaction Runtime

## Purpose

This release upgrades WebChat from a text-only chat entry point into a structured customer interaction runtime:

```text
Customer input
→ safe intent detection
→ backend card factory
→ structured card/action
→ optional trusted tool fact lookup
→ ticket transcript + audit event
→ agent-visible handoff/action/fact state
→ WebChat local-only delivery semantics
```

## End-to-end flow

1. Visitor opens the widget.
2. `POST /api/webchat/init` creates or resumes a conversation.
3. Visitor sends text with an optional `client_message_id`.
4. Backend writes `webchat_messages`, `ticket_comments`, and `ticket_events`.
5. Backend detects intent and safely creates an allowlisted card when useful.
6. When Tracking Fact MVP flags are enabled and a tracking number is present, backend calls the OpenClaw Bridge read-only `/tools/speedaf_lookup` endpoint, redacts and normalizes the result, and injects only the sanitized fact summary into the AI prompt.
7. Visitor clicks a card action when a rendered card is present.
8. `POST /api/webchat/conversations/{conversation_id}/actions` validates the card/action and writes `webchat_card_actions`.
9. Admin thread shows text, card, action, handoff state, and tracking fact audit metadata.
10. Agent reply remains a WebChat local delivery record and does not enter external provider dispatch.

## Compatibility

Existing WebChat behavior is preserved:

- Existing `body` field remains readable.
- Existing send and poll API paths remain available.
- Old clients that do not send `client_message_id` still work.
- Old clients that ignore `message_type` still receive `body`.
- Tracking Fact MVP is disabled by default and does not change runtime behavior until explicitly enabled through feature flags.

## New capabilities

- `message_type`: `text`, `system`, `card`, `action`, `attachment`.
- `payload_json`: card/action payload.
- `metadata_json`: safety, intent, generated-by, fact evidence, and local-only delivery metadata.
- `client_message_id`: visitor-side idempotency key.
- `delivery_status`: `sending`, `sent`, `failed`, `delivered` style state.
- `action_status`: `pending`, `submitted`, `expired`, `cancelled` style state.
- `webchat_card_actions`: durable action audit table.
- Tracking Fact MVP: sanitized tool facts can set `fact_evidence_present=true` for AI text replies only.

## Current release boundaries

Fully implemented in this release:

- Text message send/poll/reply compatibility.
- `after_id` incremental polling contract.
- Visitor `client_message_id` idempotency for message send.
- Quick replies card generation is gated by `WEBCHAT_STATIC_QUICK_REPLIES_MODE=legacy`.
- Handoff card generation and widget rendering.
- Card action submission.
- Durable action audit via `webchat_card_actions`.
- Admin thread display for text/card/action/handoff messages.
- WebChat local-only outbound semantics for ACK, AI reply, safe fallback, card delivery, and handoff ACK.
- Feature-gated Tracking Fact text reply path: tracking number extraction, Bridge lookup, PII redaction, fact prompt injection, fact gate evidence, and metadata/TicketEvent audit.

Schema-only / fallback-first in this release:

- `tracking_status` card rendering remains disabled for Phase 1.
- `address_confirmation`
- `reschedule_picker`
- `photo_upload_request`
- `csat`

Not enabled in this release:

- Binary photo upload handling.
- Customer-facing tracking-status cards.
- Raw tracking result storage.
- Automated address-change success confirmation.
- Automated reschedule success confirmation.
- Automated refund / compensation / customs handling.
- External provider outbound dispatch.
- WebSocket/SSE realtime transport.

## AI safety model

AI does not render HTML or arbitrary JSON directly. AI or rules can produce an intent. Backend-owned code maps that intent to allowlisted card builders:

- `build_quick_replies_card()` when legacy mode is explicitly enabled.
- `build_handoff_card()`
- schema-only/safe fallbacks for tracking, address confirmation, reschedule, photo upload, and CSAT

High-risk intents such as refund, compensation, lost parcel, damage, customs, complaint, address change, or reschedule are routed to handoff or safe text fallback.

Tracking Fact MVP does not let AI call arbitrary tools. The backend calls one controlled Bridge endpoint, converts the result into `TrackingFactResult`, redacts PII, and passes only a trusted fact summary to the AI prompt.

## Fact gate

Without tool/database evidence, WebChat must not promise:

- confirmed parcel status
- successful address change
- successful reschedule
- compensation/refund result
- customs result
- driver contact or delivery time

The fact gate allows factual parcel-status language only when a trusted tool/database result is explicitly attached.

Tracking Fact MVP sets `fact_evidence_present=true` only when all of these are true:

1. `WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED=true`.
2. A tracking number is present in the ticket/message/history.
3. Bridge `/tools/speedaf_lookup` returns a successful result.
4. The result is normalized into a `TrackingFactResult`.
5. `pii_redacted=true`.

If lookup is missing, disabled, timed out, failed, or not redacted, the system keeps `fact_evidence_present=false` and falls back to safe tracking-required / human-review text.

## Observability events

The implementation logs structured events including:

- `webchat_session_created`
- `webchat_message_received`
- `webchat_message_sent`
- `webchat_message_polled`
- `webchat_card_generated`
- `webchat_card_action_submitted`
- `webchat_card_action_rejected`
- `webchat_handoff_triggered`
- `webchat_fact_gate_blocked`
- `webchat_tracking_fact_lookup_result`

TicketEvent audit payloads include:

- `webchat_tracking_fact_used`
- `webchat_tracking_fact_not_used`

Tokens, raw tool results, full tracking payloads, and full PII are not logged.

## Current limits

- Realtime transport remains polling, optimized with `after_id` and backoff.
- `handoff` is fully rendered in the widget.
- `quick_replies` is legacy-only and disabled by default.
- Tracking Fact Phase 1 is AI text reply only; `tracking_status` cards remain off by default.
- Tracking/address/reschedule/photo/CSAT cards beyond handoff are schema-safe or fallback-first, not full business workflows yet.
- Photo upload is not enabled in this release.
- No database migration is required for Tracking Fact MVP; metadata and TicketEvent carry the audit trail.
- Production deployment still requires app/worker rebuild, staging smoke, Bridge health smoke, and WebChat full admin smoke.
