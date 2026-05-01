# WebChat Structured Interaction Runtime

## Purpose

This release upgrades WebChat from a text-only chat entry point into a structured customer interaction runtime:

```text
Customer input
→ safe intent detection
→ backend card factory
→ structured card/action
→ ticket transcript + audit event
→ agent-visible handoff/action state
→ WebChat local-only delivery semantics
```

## End-to-end flow

1. Visitor opens the widget.
2. `POST /api/webchat/init` creates or resumes a conversation.
3. Visitor sends text with an optional `client_message_id`.
4. Backend writes `webchat_messages`, `ticket_comments`, and `ticket_events`.
5. Backend detects intent and safely creates an allowlisted card when useful.
6. Visitor clicks a card action.
7. `POST /api/webchat/conversations/{conversation_id}/actions` validates the card/action and writes `webchat_card_actions`.
8. Admin thread shows text, card, action, and handoff state.
9. Agent reply remains a WebChat local delivery record and does not enter external provider dispatch.

## Compatibility

Existing WebChat behavior is preserved:

- Existing `body` field remains readable.
- Existing send and poll API paths remain available.
- Old clients that do not send `client_message_id` still work.
- Old clients that ignore `message_type` still receive `body`.

## New capabilities

- `message_type`: `text`, `system`, `card`, `action`, `attachment`.
- `payload_json`: card/action payload.
- `metadata_json`: safety, intent, generated-by, and local-only delivery metadata.
- `client_message_id`: visitor-side idempotency key.
- `delivery_status`: `sending`, `sent`, `failed`, `delivered` style state.
- `action_status`: `pending`, `submitted`, `expired`, `cancelled` style state.
- `webchat_card_actions`: durable action audit table.

## AI safety model

AI does not render HTML or arbitrary JSON directly. AI or rules can produce an intent. Backend-owned code maps that intent to allowlisted card builders:

- `build_quick_replies_card()`
- `build_handoff_card()`
- schema-only/safe fallbacks for tracking, address confirmation, reschedule, photo upload, and CSAT

High-risk intents such as refund, compensation, lost parcel, damage, customs, complaint, address change, or reschedule are routed to handoff or safe text fallback.

## Fact gate

Without tool/database evidence, WebChat must not promise:

- confirmed parcel status
- successful address change
- successful reschedule
- compensation/refund result
- customs result
- driver contact or delivery time

The first release includes a lightweight fact gate service and records `fact_evidence_present=false` in generated metadata.

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

Tokens and full PII are not logged.

## Current limits

- Realtime transport remains polling, optimized with `after_id` and backoff.
- `quick_replies` and `handoff` are fully rendered in the widget.
- Tracking/address/reschedule/photo/CSAT are schema-safe or fallback-first, not full business workflow yet.
- Photo upload is not enabled in this release.
