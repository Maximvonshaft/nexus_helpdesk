# Chatwoot Sidecar Audit and NexusDesk Bridge PoC Design

Issue: #237

Status: design-only, read-only audit. No Chatwoot source code is modified by this report.

## Executive Verdict

Chatwoot should be treated as a sidecar/reference for conversation operations, not as the NexusDesk logistics runtime.

The strongest reusable ideas are:

1. Inbox as a channel-scoped operational container.
2. ContactInbox as the channel identity join between Contact and Inbox.
3. Conversation as a lightweight customer-thread object with status, priority, assignee, team, custom attributes, SLA policy, labels, and message timeline.
4. Message as a polymorphic, direction-aware, status-aware timeline event with attachments, private notes, source IDs, and webhook payload support.
5. API Channel + Webhook as the preferred sidecar bridge surface.
6. Agent console UX patterns: three-pane inbox, conversation timeline, contact/context side panel, reply composer, private notes, templates, article insertion, attachments, drafts, typing status, read/unread split, and channel-specific reply restrictions.

NexusDesk should keep ownership of logistics workflows, AI/OpenClaw orchestration, parcel/tracking/POD/exception/SLA/audit objects, WebCall, Speedaf integrations, and production business rules.

Recommended bridge pattern:

```text
Chatwoot API Channel / Webhook
        |
        v
Nexus Chatwoot Sidecar Adapter
        |
        +--> normalize event
        +--> verify security boundary
        +--> dedupe/idempotency
        +--> map Chatwoot IDs to Nexus IDs
        +--> write WebchatConversation/WebchatMessage/TicketEvent/TicketOutboundMessage
        |
        v
NexusDesk Core remains source of truth
```

## Scope and Evidence Baseline

Pinned Chatwoot sidecar:

- Repository: `chatwoot/chatwoot`
- Pinned release/tag intent: `v4.14.0`
- Pinned commit audited: `81cb75b62feaaea25d8d4baaf099c46a8eb65c15`
- Nexus branch used for this report: `docs/chatwoot-audit-bridge-poc-237`
- Nexus baseline commit: `731a652f452a27232a65cb0e485e4ebce242b6f6`

Primary Chatwoot evidence anchors:

- `app/models/inbox.rb`
- `app/models/channel/api.rb`
- `app/models/channel/web_widget.rb`
- `app/models/contact.rb`
- `app/models/contact_inbox.rb`
- `app/models/conversation.rb`
- `app/models/message.rb`
- `app/models/webhook.rb`
- `app/models/concerns/webhook_secretable.rb`
- `lib/webhooks/trigger.rb`
- `config/routes.rb`
- `app/controllers/api/v1/widget/base_controller.rb`
- `app/controllers/api/v1/widget/conversations_controller.rb`
- `app/controllers/api/v1/widget/messages_controller.rb`
- `app/controllers/public/api/v1/inboxes/conversations_controller.rb`
- `app/controllers/public/api/v1/inboxes/messages_controller.rb`
- `app/controllers/api/v1/accounts/conversations_controller.rb`
- `app/controllers/api/v1/accounts/conversations/messages_controller.rb`
- `app/javascript/entrypoints/widget.js`
- `app/javascript/widget/api/endPoints.js`
- `app/javascript/dashboard/components/widgets/conversation/MessagesView.vue`
- `app/javascript/dashboard/components/widgets/conversation/ReplyBox.vue`

Primary NexusDesk evidence anchors:

- `backend/app/webchat_models.py`
- `backend/app/api/webchat_fast.py`
- `backend/app/services/webchat_fast_session_service.py`
- `backend/app/services/webchat_fast_idempotency_db.py`
- `backend/app/models.py`
- `backend/app/api/integration.py`
- `frontend/app.js`

## Workstream 1: Chatwoot Core Structure Audit

### 1. Inbox Structure

`Inbox` is the central operational boundary for Chatwoot channels.

Evidence from `app/models/inbox.rb`:

- `Inbox` belongs to `account`.
- `Inbox` belongs to polymorphic `channel` through `channel_id` and `channel_type`.
- `Inbox` has many `contact_inboxes`, `contacts`, `inbox_members`, `members`, `conversations`, `messages`, `webhooks`, and integration hooks.
- It supports assignment-related fields and relationships: `enable_auto_assignment`, `auto_assignment_config`, `inbox_assignment_policy`, `assignment_policy`, `agent_bot_inbox`, `agent_bot`.
- It has channel predicate helpers: `web_widget?`, `api?`, `email?`, `whatsapp?`, `telegram?`, `twilio?`, `facebook?`, `instagram?`, `sms?`, `twitter?`, `tiktok?`.

Engineering value for Nexus:

- Nexus should not copy the Rails model, but it should absorb the concept that a channel/inbox is not only a transport. It is a routing, assignment, team, automation, and policy boundary.
- Nexus `ChannelAccount` already partially covers provider/account/market priority. It should evolve toward a channel operational profile containing team/routing/SLA/feature flags/reply-window policy.

### 2. API Channel

`Channel::Api` is the best Chatwoot bridge surface for Nexus.

Evidence from `app/models/channel/api.rb`:

- `channel_api` has `identifier`, `secret`, `hmac_mandatory`, `hmac_token`, `webhook_url`, `additional_attributes`, and `account_id`.
- It includes `Channelable` and `WebhookSecretable`.
- It has a configurable `agent_reply_time_window` inside `additional_attributes`.

Engineering value for Nexus:

- For PoC, use Chatwoot API Channel rather than trying to splice into the Website Widget runtime.
- API Channel has a cleaner machine-to-machine boundary and explicit webhook URL/HMAC concepts.
- API Channel status update is explicitly checked in `app/controllers/api/v1/accounts/conversations/messages_controller.rb` through `ensure_api_inbox`, which only allows message status updates for API inboxes.

### 3. Web Widget Structure

Chatwoot Website Widget is useful for UX and architecture inspiration, but it is not the preferred Nexus bridge surface.

Evidence from `app/models/channel/web_widget.rb`:

- `Channel::WebWidget` stores `allowed_domains`, `continuity_via_email`, `feature_flags`, `hmac_mandatory`, `hmac_token`, `pre_chat_form_enabled`, `pre_chat_form_options`, `reply_time`, `website_token`, `website_url`, `welcome_title`, `welcome_tagline`, and `widget_color`.
- `web_widget_script` injects `/packs/js/sdk.js` and runs `window.chatwootSDK.run({ websiteToken, baseUrl })`.
- `create_contact_inbox` uses `ContactInboxWithContactBuilder`.

Evidence from `app/javascript/entrypoints/widget.js`:

- Widget is a Vue app mounted onto `#app`.
- It initializes Vue store, router, i18n, DOMPurify, FormKit validation rules, and `ActionCableConnector` with `window.chatwootPubsubToken`.

Evidence from `app/javascript/widget/api/endPoints.js`:

- Widget creates conversations via `/api/v1/widget/conversations`.
- Widget sends messages and attachments via `/api/v1/widget/messages`.
- Payload includes contact identity, message content, timestamp, referrer URL, custom attributes, labels, and attachments.

Engineering value for Nexus:

- Absorb the widget architecture principles: tokenized website channel, domain allowlist, HMAC option, pre-chat form, continuity by email, color/branding, feature flags, and custom attributes.
- Do not make Nexus depend on Chatwoot widget internals. The `/api/v1/widget/*` endpoints are Chatwoot runtime implementation details, not the cleanest integration boundary.

### 4. Contact and ContactInbox Structure

`ContactInbox` is one of the most valuable data-model ideas for Nexus.

Evidence from `app/models/contact.rb`:

- `Contact` has account-scoped `email`, `phone_number`, `identifier`, `additional_attributes`, `custom_attributes`, `contact_type`, `country_code`, and activity fields.
- It has many `conversations`, `contact_inboxes`, `inboxes`, and `messages` as sender.
- `contact_type` supports `visitor`, `lead`, `customer`.

Evidence from `app/models/contact_inbox.rb`:

- `ContactInbox` joins `contact`, `inbox`, and `source_id`.
- It has `hmac_verified` and `pubsub_token`.
- It enforces uniqueness on `(inbox_id, source_id)`.
- `current_conversation` is currently the last conversation for that contact/inbox pair.

Engineering value for Nexus:

- Nexus currently maps public WebChat identity through deterministic `tenant_key`, `channel_key`, `session_id`, and `Customer.external_ref` in `webchat_fast_session_service.py`.
- For Chatwoot sidecar integration, Nexus needs an explicit identity mapping table similar to `ContactInbox` because the same end user may appear under multiple Chatwoot inboxes, API channels, web widgets, WhatsApp numbers, or email addresses.

### 5. Conversation Structure

Chatwoot conversation is a customer-thread object, not a logistics case object.

Evidence from `app/models/conversation.rb`:

- `Conversation` has `additional_attributes`, `custom_attributes`, `last_activity_at`, `priority`, `status`, `uuid`, `waiting_since`, `first_reply_created_at`, `agent_last_seen_at`, `assignee_last_seen_at`, and `contact_last_seen_at`.
- It belongs to `account`, `inbox`, `assignee`, `assignee_agent_bot`, `contact`, `contact_inbox`, `team`, `campaign`, and optional `sla_policy`.
- It has many `messages`, `conversation_participants`, `attachments`, `notifications`, and reporting events.
- Status enum: `open`, `resolved`, `pending`, `snoozed`.
- Priority enum: `low`, `medium`, `high`, `urgent`.
- It has methods for `can_reply?`, `toggle_status`, `toggle_priority`, `bot_handoff!`, unread messages, and assignee identity.

Engineering value for Nexus:

- Nexus should absorb the conversation-level fields: status, priority, assignee, team, last activity, last seen, custom attributes, waiting-since, and SLA relationship.
- Nexus should not treat Chatwoot conversation as the same thing as Nexus `Ticket`. A Chatwoot conversation can map to zero, one, or multiple Nexus operational cases depending on tracking number, issue type, parcel event, or handoff state.

### 6. Message Structure

Chatwoot messages are timeline events with direction, status, content type, private notes, attachments, and external source identifiers.

Evidence from `app/models/message.rb`:

- `Message` has `content`, `content_attributes`, `additional_attributes`, `external_source_ids`, `message_type`, `content_type`, `private`, `status`, `source_id`, `sender_type`, and `sender_id`.
- `message_type`: `incoming`, `outgoing`, `activity`, `template`.
- `content_type` includes text, structured inputs, forms, article, email, CSAT, integrations, stickers, and `voice_call`.
- It belongs to `account`, `inbox`, `conversation`, and polymorphic sender.
- It has many `attachments`.
- `webhook_data` contains account, additional/content attributes, content, conversation, inbox, message type, private flag, sender, source ID, and attachments.
- `content_for_llm` can derive content from text, audio transcription, or attachment placeholder.
- After create, it reopens conversation if needed, updates activity, dispatches events, sends replies, updates contact activity, and maintains `waiting_since` behavior.

Engineering value for Nexus:

- Nexus `WebchatMessage` already covers direction, body, metadata, client_message_id, status, AI turn, safety fields, and ticket link.
- Nexus should absorb `private` notes and external source ID semantics more explicitly for cross-channel and Chatwoot sidecar operations.

### 7. Webhook Structure

Chatwoot webhooks are the correct inbound event source for a sidecar PoC.

Evidence from `app/models/webhook.rb`:

- `Webhook` has `url`, `secret`, `subscriptions`, `webhook_type`, `account_id`, and optional `inbox_id`.
- Webhooks can be account-scoped or inbox-scoped.
- Allowed events include `conversation_status_changed`, `conversation_updated`, `conversation_created`, `contact_created`, `contact_updated`, `message_created`, `message_updated`, `webwidget_triggered`, `inbox_created`, `inbox_updated`, `conversation_typing_on`, and `conversation_typing_off`.

Evidence from `app/models/concerns/webhook_secretable.rb`:

- Webhook secrets use `has_secure_token` and can be encrypted if Chatwoot encryption is configured.

Evidence from `lib/webhooks/trigger.rb`:

- Chatwoot sends webhook requests with JSON body.
- Headers include `Content-Type: application/json` and `Accept: application/json`.
- If a delivery ID exists, it sends `X-Chatwoot-Delivery`.
- If a secret exists, it sends `X-Chatwoot-Timestamp` and `X-Chatwoot-Signature` in the format `sha256=<hmac>` where the signed material is `timestamp.body` using HMAC-SHA256.
- This is enough for Nexus to implement signature verification without guessing.

### 8. Chatwoot Public and Widget APIs

Evidence from `config/routes.rb`:

- Account-scoped API: `/api/v1/accounts/:account_id/conversations`, nested messages, assignments, labels, participants, draft messages, custom attributes, transcript, status, priority, typing, last seen, attachments, and inbox assistant.
- Widget API: `/api/v1/widget/conversations`, `/api/v1/widget/messages`, `/api/v1/widget/contact`, `/api/v1/widget/inbox_members`, labels, direct uploads, campaigns, events.
- Public contact API: `/public/api/v1/inboxes/:inbox_id/contacts/:contact_id/conversations/:id/messages` and related conversation actions.

Evidence from `app/controllers/api/v1/widget/base_controller.rb`:

- Widget-side conversation creation stores browser language, browser/device/platform, timestamp, referrer URL, and custom attributes.
- Widget messages are always created as incoming messages from the contact.

Evidence from `app/controllers/api/v1/widget/conversations_controller.rb` and `app/controllers/api/v1/widget/messages_controller.rb`:

- Widget can create/update contacts, create conversations, create first messages, send messages/attachments, update last seen, toggle typing, toggle status, set/destroy custom attributes, and apply labels.

Evidence from `app/controllers/api/v1/accounts/conversations_controller.rb` and `app/controllers/api/v1/accounts/conversations/messages_controller.rb`:

- Agent/account API can list/search/filter conversations, create conversations, toggle status/priority/typing, update last seen/custom attributes, create messages through `Messages::MessageBuilder`, retry messages, translate messages, and update message status for API inboxes only.

Engineering value for Nexus:

- For inbound from Chatwoot to Nexus, use Chatwoot Webhooks.
- For outbound from Nexus to Chatwoot, use account-scoped API Channel conversation/message APIs.
- Avoid using `/api/v1/widget/*` for Nexus server-side integration; that layer is designed for browser widget sessions.

## Workstream 2: What Nexus Should Absorb

### UI Patterns Worth Absorbing

1. Three-pane agent workspace:
   - conversation list / queue
   - active conversation timeline
   - contact, parcel, SLA, routing, and AI context side panel

2. Timeline model:
   - split read/unread groups
   - typing state
   - message status
   - attachments
   - activity messages
   - private notes
   - bot/AI/system/human author separation

3. Reply composer:
   - reply vs private note mode
   - attachment upload and paste handling
   - draft persistence
   - keyboard send shortcut
   - quote/reply-to behavior
   - templates/canned responses
   - article insertion
   - AI/copywriter panel
   - channel-specific restrictions and reply-window warnings

4. Contact side panel:
   - contact identity
   - custom attributes
   - previous conversations
   - active cases
   - labels/tags
   - preferred channel

5. Operational controls:
   - status, priority, team, assignee
   - labels
   - SLA indicator
   - last activity / waiting since
   - handoff/bot status

Nexus current `frontend/app.js` is functional but still more like a custom admin/case editor. It has role gates, case editor state, selected ticket/case, and operational cards, but it does not yet match the depth of Chatwoot's conversation workspace, timeline, composer, draft, private-note, attachment, and side-panel UX.

### Data Model Ideas Worth Absorbing

Nexus should absorb these patterns, not necessarily these exact Rails tables:

1. `Inbox` -> Nexus `ChannelAccount` / future `ChannelProfile`
   - one channel/inbox should own routing, assignment, feature flags, reply-window policy, team, SLA default, widget/domain policy, and provider credentials reference.

2. `ContactInbox` -> new Nexus `ExternalContactLink` or `ChannelIdentityLink`
   - maps provider account/inbox/source identity to Nexus `Customer`, `WebchatConversation`, and optionally `Ticket`.

3. `Conversation` -> strengthen Nexus `WebchatConversation`
   - add/standardize status, priority, assignee/team, custom attributes, last activity, waiting since, last seen, external conversation refs.

4. `Message` -> strengthen Nexus `WebchatMessage`
   - add private note flag, external source IDs, source message ID, provider delivery status, content attributes, attachment grouping, and webhook-normalized event source metadata.

5. `Webhook` -> new or extended Nexus integration event source
   - support event subscriptions, delivery ID, HMAC signature verification, secret reference, retry/dedupe, and structured normalized event log.

### What Nexus Should Keep Independent

Do not migrate these into Chatwoot or make Chatwoot the source of truth:

- Parcel/tracking state.
- Delivery attempt lifecycle.
- POD evidence, GPS, timestamp, image evidence, address verification.
- Speedaf API integration and work-order creation.
- OpenClaw/MCP/Codex AI runtime.
- AI decision records, tool calls, handoff policy, fact-gated tracking replies.
- Nexus `Ticket`, `TicketEvent`, `TicketOutboundMessage`, `TicketAIIntake`, `AdminAuditLog`.
- Market/country/channel-account routing.
- WebCall runtime and voice lifecycle.
- Swiss compliance/audit policy.

## Nexus Current Capability Mapping

Current Nexus already has strong domain-side primitives:

- `WebchatConversation`, `WebchatMessage`, `WebchatAITurn`, `WebchatEvent`, and `WebchatCardAction` in `backend/app/webchat_models.py`.
- `/api/webchat/fast-reply` and `/api/webchat/fast-reply/stream` in `backend/app/api/webchat_fast.py`.
- deterministic public session identity and customer/ticket linkage in `backend/app/services/webchat_fast_session_service.py`.
- strict webchat idempotency through `WebchatFastIdempotency` in `backend/app/services/webchat_fast_idempotency_db.py`.
- `Customer`, `Ticket`, `TicketEvent`, `TicketOutboundMessage`, `TicketAIIntake`, `ChannelAccount`, `Market`, `Team`, `AdminAuditLog`, and `OpenClaw*` models in `backend/app/models.py`.
- secured integration endpoints under `/api/v1/integration` with authentication, rate limiting, idempotency, profile lookup, and task creation in `backend/app/api/integration.py`.

Gap versus Chatwoot:

1. Nexus has stronger logistics workflow primitives.
2. Chatwoot has stronger conversation workspace UX and provider-channel abstraction.
3. Nexus lacks an explicit Chatwoot-style `ContactInbox` equivalent for cross-provider identity mapping.
4. Nexus `WebchatMessage` has good internal fields, but needs provider message IDs and private-note semantics for sidecar channels.
5. Nexus frontend needs a deeper agent conversation layout to support production customer-service speed.

## Workstream 3: Chatwoot-to-Nexus Sidecar Bridge PoC Design

### PoC Goal

Prove that Chatwoot can act as a sidecar customer conversation/inbox layer while Nexus remains the source of truth for logistics, AI, tickets, and audit.

The first PoC should cover:

1. Inbound Chatwoot webhook -> Nexus normalized event.
2. Nexus message/customer/conversation/ticket mapping.
3. Nexus AI/OpenClaw or human reply -> Chatwoot API Channel outgoing message.
4. Idempotency, ordering, security, and rollback.

### Proposed Files

Do not implement in this report PR. Use this as the next implementation plan after review.

```text
backend/app/api/chatwoot_sidecar.py
backend/app/chatwoot_sidecar_models.py
backend/app/services/chatwoot_sidecar/__init__.py
backend/app/services/chatwoot_sidecar/security.py
backend/app/services/chatwoot_sidecar/normalize.py
backend/app/services/chatwoot_sidecar/idempotency.py
backend/app/services/chatwoot_sidecar/identity.py
backend/app/services/chatwoot_sidecar/client.py
backend/app/services/chatwoot_sidecar/bridge.py
backend/alembic/versions/<revision>_chatwoot_sidecar_bridge.py
tests/test_chatwoot_sidecar_webhook.py
tests/test_chatwoot_sidecar_identity.py
tests/test_chatwoot_sidecar_outbound.py
```

### Proposed Configuration

```text
CHATWOOT_SIDECAR_ENABLED=false
CHATWOOT_BASE_URL=https://chatwoot.example.com
CHATWOOT_ACCOUNT_ID=<configured per environment>
CHATWOOT_API_ACCESS_TOKEN=<secret manager reference only>
CHATWOOT_WEBHOOK_SECRET=<secret manager reference only>
CHATWOOT_ALLOWED_ACCOUNT_IDS=...
CHATWOOT_ALLOWED_INBOX_IDS=...
CHATWOOT_OUTBOUND_TIMEOUT_MS=5000
CHATWOOT_WEBHOOK_DEDUPE_TTL_SECONDS=86400
```

Use secret references in production. Do not commit secrets to `.env` examples except placeholder names.

### Proposed Tables

#### `chatwoot_sidecar_bindings`

Purpose: bind Chatwoot account/inbox/channel to Nexus tenant/channel account.

Fields:

```text
id
tenant_key
nexus_channel_key
nexus_channel_account_id
chatwoot_account_id
chatwoot_inbox_id
chatwoot_channel_type
chatwoot_api_channel_identifier
webhook_secret_ref
api_token_ref
enabled
created_at
updated_at
```

Constraints:

- unique `(tenant_key, chatwoot_account_id, chatwoot_inbox_id)`
- index `(enabled, tenant_key)`

#### `chatwoot_identity_links`

Purpose: Chatwoot equivalent of a Nexus-owned `ContactInbox` mapping.

Fields:

```text
id
tenant_key
chatwoot_account_id
chatwoot_inbox_id
chatwoot_contact_id
chatwoot_contact_inbox_id
chatwoot_contact_inbox_source_id
chatwoot_conversation_id
chatwoot_conversation_display_id
chatwoot_message_source_id
nexus_customer_id
nexus_webchat_conversation_id
nexus_ticket_id
last_chatwoot_message_id
last_seen_event_at
created_at
updated_at
```

Constraints:

- unique `(tenant_key, chatwoot_account_id, chatwoot_inbox_id, chatwoot_conversation_id)`
- index `(nexus_customer_id)`
- index `(nexus_webchat_conversation_id)`
- index `(nexus_ticket_id)`

#### `chatwoot_event_dedupe`

Purpose: webhook idempotency and replay protection.

Fields:

```text
id
tenant_key
chatwoot_account_id
chatwoot_inbox_id
event_name
chatwoot_delivery_id
source_event_id
source_message_id
source_conversation_id
payload_hash
status
response_json
error_code
locked_until
attempt_count
created_at
updated_at
expires_at
```

Constraints:

- unique `(tenant_key, event_name, chatwoot_delivery_id)` when delivery ID is present
- otherwise unique `(tenant_key, event_name, source_conversation_id, source_message_id, payload_hash)`

### Inbound Event Path

```text
Chatwoot webhook
  -> POST /api/v1/integration/chatwoot/webhook
  -> verify delivery header and HMAC signature
  -> resolve binding by account_id/inbox_id
  -> dedupe event
  -> normalize payload
  -> upsert Chatwoot identity link
  -> upsert Nexus Customer
  -> upsert/reuse WebchatConversation
  -> append WebchatMessage or TicketEvent
  -> optionally trigger Nexus AI/handoff/ticket logic
```

Initial supported events:

- `message_created`
- `message_updated`
- `conversation_created`
- `conversation_updated`
- `conversation_status_changed`
- `contact_created`
- `contact_updated`

Later supported events:

- `conversation_typing_on`
- `conversation_typing_off`
- `webwidget_triggered`
- `inbox_created`
- `inbox_updated`

Normalization contract:

```json
{
  "source": "chatwoot",
  "event_name": "message_created",
  "tenant_key": "default",
  "chatwoot": {
    "account_id": 1,
    "inbox_id": 2,
    "conversation_id": 123,
    "conversation_display_id": 456,
    "message_id": 789,
    "contact_id": 321,
    "source_id": "provider-source-id"
  },
  "nexus": {
    "customer_id": null,
    "webchat_conversation_id": null,
    "ticket_id": null
  },
  "message": {
    "direction": "visitor|agent|ai|system",
    "body_text": "...",
    "private": false,
    "message_type": "text",
    "provider_status": "sent|delivered|read|failed"
  },
  "raw": {}
}
```

### Outbound Path

```text
Nexus human/AI reply
  -> create TicketOutboundMessage(status=pending, channel=web_chat)
  -> Chatwoot sidecar client resolves identity link
  -> POST Chatwoot account-scoped conversation message API
  -> store Chatwoot message ID/source ID in TicketOutboundMessage.provider_message_id
  -> update status sent/failed
  -> append WebchatMessage(direction=agent|ai)
```

For the first PoC, outbound should only send plain text to one Chatwoot API inbox conversation. Attachments, private notes, templates, and message status callbacks can be phased in after the text round-trip passes.

### Identity Mapping

Recommended mapping:

```text
Chatwoot Account           -> Nexus tenant_key / organization boundary
Chatwoot Inbox             -> Nexus channel_key + ChannelAccount
Chatwoot API Channel       -> Nexus sidecar transport binding
Chatwoot Contact           -> Nexus Customer
Chatwoot ContactInbox      -> Nexus ChatwootIdentityLink channel identity
Chatwoot Conversation      -> Nexus WebchatConversation and/or Ticket
Chatwoot Message           -> Nexus WebchatMessage and/or TicketOutboundMessage
Chatwoot Webhook Delivery  -> Nexus ChatwootEventDedupe row
```

Rules:

1. Never key identity only by `contact_id`; always scope by account/inbox.
2. Prefer `contact_inbox.source_id` for channel identity when available.
3. Do not automatically create a Nexus `Ticket` for every Chatwoot conversation. Create a ticket only when AI/handoff/policy/logistics intent requires an operational case.
4. Store all external IDs in sidecar mapping rows and message metadata for traceability.
5. Keep `Customer.external_ref` stable and avoid overwriting known email/phone with blank Chatwoot data.

### Idempotency and Ordering

Inbound idempotency:

- Prefer `X-Chatwoot-Delivery` when present.
- Fallback to deterministic key: `(tenant_key, event_name, conversation_id, message_id, payload_hash)`.
- Return replayed result for duplicate completed events.
- Return 202 for currently processing events.
- Return 409 for same idempotency key with different payload hash.

Outbound idempotency:

- Use Nexus `TicketOutboundMessage.id` as canonical outbound idempotency key.
- Store Chatwoot provider message ID after success.
- If retry sees existing provider message ID, do not send again unless explicitly forced by an operator action.

Out-of-order handling:

- If `message_created` arrives before `conversation_created`, create or hydrate a placeholder `WebchatConversation` from the message payload and schedule a hydration job.
- If `contact_updated` arrives after messages, update the `Customer` only if new data is non-empty and does not downgrade verified data.
- If `conversation_status_changed` arrives before the first message, store a `TicketEvent` or `WebchatEvent` and apply once the conversation link exists.

### Security Plan

1. HMAC verification
   - Verify `X-Chatwoot-Timestamp` and `X-Chatwoot-Signature` when `CHATWOOT_WEBHOOK_SECRET` is configured.
   - Signature material must be `timestamp.raw_body` using HMAC-SHA256, matching Chatwoot `lib/webhooks/trigger.rb`.
   - Reject old timestamps beyond a short replay window, for example 5 minutes.

2. Binding enforcement
   - Accept only configured `chatwoot_account_id` and `chatwoot_inbox_id` pairs.
   - Reject unknown account/inbox combinations.

3. Secrets
   - Store secrets through deployment secret manager or environment secret reference.
   - Never log API tokens, webhook secrets, raw authorization headers, or customer PII-heavy payloads.

4. Rate limiting
   - Reuse Nexus integration rate-limit style or add `chatwoot_webhook` bucket.

5. PII minimization
   - Persist normalized contact fields required for support.
   - Store raw payload only in bounded, redacted audit/debug form if needed.
   - Keep source payload retention short unless compliance requires longer storage.

6. Audit
   - Record inbound event accepted/rejected status.
   - Record outbound attempt and provider response status.
   - Tie every operational mutation to a `TicketEvent`, `WebchatEvent`, or `AdminAuditLog` equivalent.

### Test Plan

Unit tests:

- HMAC signature valid/invalid/expired/missing.
- Payload normalization for `message_created`, `message_updated`, `conversation_created`, `conversation_updated`, `conversation_status_changed`, `contact_created`, `contact_updated`.
- Identity mapping for same contact across multiple inboxes.
- Idempotent replay with `X-Chatwoot-Delivery`.
- Fallback idempotency without delivery ID.
- Out-of-order message before conversation.
- PII redaction in logs.

Integration tests:

- Mock Chatwoot webhook creates Nexus `Customer`, `WebchatConversation`, and `WebchatMessage` without creating a `Ticket` unless handoff policy says so.
- Mock logistics intent creates or links a Nexus `Ticket`.
- Nexus outbound text reply creates `TicketOutboundMessage`, calls mocked Chatwoot API, and records provider message ID.
- Duplicate webhook does not create duplicate messages.
- Failed outbound marks `TicketOutboundMessage` failed with retry metadata.

Smoke tests:

```bash
pytest tests/test_chatwoot_sidecar_webhook.py
pytest tests/test_chatwoot_sidecar_identity.py
pytest tests/test_chatwoot_sidecar_outbound.py
```

Optional server probe after implementation:

```bash
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

### Rollback Plan

1. Set `CHATWOOT_SIDECAR_ENABLED=false`.
2. Disable Chatwoot webhook endpoint at Chatwoot side or remove its subscription.
3. Stop outbound worker path by feature flag.
4. Keep sidecar mapping rows for forensic continuity; do not drop tables during incident rollback.
5. Re-run Nexus WebChat Fast smoke tests to confirm existing customer path still works.
6. If a migration caused runtime issues, revert the migration through the standard Alembic rollback path only after confirming no production event processing depends on it.

## Recommended Next PRs

### PR A: Read-only scaffold and schema

- Add `chatwoot_sidecar_models.py`.
- Add migration for bindings, identity links, and dedupe rows.
- Add config settings with default disabled.
- Add tests for model constraints.

No runtime endpoint enabled.

### PR B: Inbound webhook endpoint

- Add `/api/v1/integration/chatwoot/webhook`.
- Implement HMAC verification and account/inbox allowlist.
- Normalize message/conversation/contact events.
- Persist event dedupe and identity mapping.
- Append Nexus `WebchatMessage` / `WebchatEvent` only.

No outbound messages yet.

### PR C: Outbound text bridge

- Implement Chatwoot API client.
- Add outbound plain-text send from Nexus to Chatwoot API channel.
- Link `TicketOutboundMessage.provider_message_id`.
- Add retry/timeout/error classification.

### PR D: Agent workspace UX absorption

- Redesign Nexus case/conversation UI around Chatwoot-inspired layout:
  - queue list
  - conversation timeline
  - parcel/context side panel
  - reply composer
  - private notes
  - templates and AI suggestion panel

## Final Decision

Proceed with Chatwoot as an isolated sidecar/reference.

Do not fork-edit Chatwoot yet.

Do not make Chatwoot a production dependency yet.

Use API Channel + Webhook as the PoC integration boundary.

NexusDesk remains the system of record for logistics operations, AI runtime, tickets, parcel state, evidence, audit, and Speedaf workflows.
