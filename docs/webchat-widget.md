# NexusDesk WebChat Widget

## What it is

NexusDesk WebChat is the public Speedaf customer support entry for customer websites. A website embeds one JavaScript snippet and visitors receive customer-visible AI replies through NexusDesk Fast Lane.

Current default mode is **fast AI**:

```text
visitor message -> POST /api/webchat/fast-reply/stream -> stream AI reply
              fallback -> POST /api/webchat/fast-reply -> AI reply
```

The browser only talks to NexusDesk. It never talks directly to private OpenClaw services and never receives OpenClaw URL, token, MCP credential, bridge credential, or internal API secret.

## Basic embed

```html
<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

Default behavior is equivalent to:

```html
<script
  src="https://YOUR_DOMAIN/webchat/widget.js"
  data-tenant="default"
  data-channel="website"
  data-webchat-mode="fast_ai"
  async>
</script>
```

## Speedaf product UI contract

The default widget is intended to look like the official Speedaf AI Support entry, not an engineering test chat panel.

Required visible elements:

- Orange launcher: `Chat with Speedaf`
- Orange header: `Speedaf Support`
- Online badge
- Speedy avatar
- Welcome message
- Quick action cards:
  - `Track my parcel`
  - `Redelivery`
  - `Refuse delivery`
  - `Delivery problem`
  - `Talk to human`
- Composer placeholder: `Type tracking number or message...`
- Attachment icon
- Orange round send button
- Safety notice: `Do not share passwords or payment codes.`
- WebCall CTA
- Typing dots
- Mobile near full-screen layout

Stable automation selectors are part of the UI contract. Do not remove these without replacing tests:

```text
speedaf-webchat-launcher
speedaf-webchat-panel
speedaf-webchat-header
speedaf-webchat-online-badge
speedaf-webchat-avatar
speedaf-webchat-quick-actions
speedaf-webchat-action-track
speedaf-webchat-action-redelivery
speedaf-webchat-action-refuse
speedaf-webchat-action-problem
speedaf-webchat-action-human
speedaf-webchat-input
speedaf-webchat-attachment
speedaf-webchat-send
speedaf-webchat-safety-notice
speedaf-webcall-cta
speedaf-parcel-status-card
speedaf-redelivery-card
speedaf-refuse-card
speedaf-handoff-card
speedaf-network-error
speedaf-ai-unavailable
```

## Optional attributes

```html
<script
  src="https://YOUR_DOMAIN/webchat/widget.js"
  data-tenant="speedaf-ch"
  data-channel="website-zurich"
  data-webchat-mode="fast_ai"
  data-title="Speedaf Support"
  data-subtitle="AI support · fast reply"
  data-assistant-name="Speedy"
  data-locale="en"
  data-welcome="Hi, how can we help you today?"
  async>
</script>
```

Supported attributes:

- `data-tenant`: tenant key, default `default`.
- `data-channel`: channel key, default `website`.
- `data-webchat-mode`: `fast_ai` or `legacy`. Default `fast_ai`.
- `data-title`: widget header title.
- `data-subtitle`: widget header subtitle.
- `data-assistant-name`: public assistant name.
- `data-locale`: locale hint.
- `data-welcome`: local welcome text before the first customer message.
- `data-input-placeholder`: input placeholder.
- `data-send-label`: send button label.
- `data-api-base`: API origin override for controlled staging/demo use.

## Fast AI mode

Fast AI mode is the default. It intentionally avoids the old ticket/message/polling chain for AI-resolvable messages.

### Runtime behavior

1. Widget appends the customer bubble locally.
2. Widget sends `POST /api/webchat/fast-reply/stream` first.
3. If stream is unavailable before visible text, widget falls back to `POST /api/webchat/fast-reply`.
4. Widget appends the AI reply bubble.
5. Retry reuses the same `client_message_id`.
6. Widget stores only recent short context in `sessionStorage`.

Fast mode does not call legacy conversation APIs unless `data-webchat-mode="legacy"` is explicitly set.

### sessionStorage

The widget stores:

- a browser-only `session_id`
- the last 5 customer/AI turns as short context

The fast path does not persist raw chat history in the browser beyond the session, and normal AI-resolved conversations are not persisted as tickets by the backend.

### Non-reply UI state

If AI is unavailable or the backend rejects invalid AI output, the widget may show non-reply UI state such as:

```text
Speedy is reconnecting...
Connection issue. Please try again.
AI unavailable
Network error / retry
```

These are status messages, not customer service replies. The widget must not display a hardcoded customer-support response such as:

```text
A support specialist will review it shortly.
We received your message and support will reply soon.
```

Customer-visible answers must come from AI.

## Handoff behavior

When backend returns `handoff_required=true`, the widget still displays the AI-generated reply immediately. Ticket creation is handled asynchronously by the backend handoff snapshot job.

The browser does not receive internal fields such as `recommended_agent_action`. Customer-facing reference should remain the tracking number or customer-provided reference, not an internal ticket number.

## WebCall CTA

The widget includes a lightweight WebCall CTA. The CTA does not request microphone access. Microphone access is requested only after the customer clicks Join on the WebCall room page.

The separate `voice-entry.js` still owns runtime voice session creation and reads `/api/webchat/voice/runtime-config`.

## Legacy mode

Legacy mode remains available only as a rollback/compatibility path:

```html
<script
  src="https://YOUR_DOMAIN/webchat/widget.js"
  data-tenant="default"
  data-channel="website"
  data-webchat-mode="legacy"
  async>
</script>
```

Legacy mode keeps the historical behavior:

1. Widget calls `POST /api/webchat/init`.
2. Backend creates or resumes `webchat_conversations` and a NexusDesk ticket.
3. Widget sends visitor messages to `POST /api/webchat/conversations/{conversation_id}/messages`.
4. Widget polls `GET /api/webchat/conversations/{conversation_id}/messages?after_id=...&limit=...`.
5. Admin UI reads `/api/webchat/admin/conversations` and `/api/webchat/admin/tickets/{ticket_id}/thread`.
6. Agent replies through `/api/webchat/admin/tickets/{ticket_id}/reply`.

Use legacy mode only for rollback, old demos, and historical conversation compatibility.

## Security boundary

- Browser may call only NexusDesk public WebChat endpoints.
- Browser must never call private AI/runtime endpoints directly.
- Browser bundle must not contain OpenClaw private URL or bearer token.
- `data-api-base` must point to NexusDesk public API, not private infrastructure.
- Card/action rendering uses DOM text APIs and does not execute arbitrary HTML.

## Demo

```text
/webchat/demo.html
```

For production customer websites, prefer the script snippet and configure `WEBCHAT_ALLOWED_ORIGINS`.

## iframe note

The JS widget is the primary embed path. Do not globally relax `X-Frame-Options` or `frame-ancestors`. If iframe embed is later required, create a dedicated `/webchat/embed` route with isolated response headers.
