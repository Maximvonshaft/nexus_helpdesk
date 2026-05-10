# NexusDesk WebChat Widget

## What it is

NexusDesk WebChat is the public customer-side support runtime for customer websites. A website embeds one JavaScript snippet and visitors receive customer-visible AI replies through NexusDesk.

Current default mode is **fast AI**:

```text
visitor message -> POST /api/webchat/fast-reply -> AI reply returned directly
```

The browser only talks to NexusDesk. It never talks directly to OpenClaw Gateway and never receives OpenClaw URL, token, MCP credential, bridge credential, or internal API secret.

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

Fast AI mode is the default. It intentionally avoids the old ticket/message/polling chain.

### Runtime behavior

1. Widget appends the customer bubble locally.
2. Widget sends `POST /api/webchat/fast-reply`.
3. Backend returns an AI-generated reply directly.
4. Widget appends the AI reply bubble.
5. Widget stores only recent short context in `sessionStorage`.

Fast mode does not call:

```text
POST /api/webchat/init
POST /api/webchat/conversations/{conversation_id}/messages
GET  /api/webchat/conversations/{conversation_id}/messages
POST /api/webchat/conversations/{conversation_id}/actions
```

Fast mode does not poll for AI reply delivery.

### sessionStorage

The widget stores:

- a browser-only `session_id`
- the last 5 customer/AI turns as short context

The fast path does not persist raw chat history in the browser beyond the session, and normal AI-resolved conversations are not persisted by the backend.

### Non-reply UI state

If AI is unavailable or the backend rejects invalid AI output, the widget may show non-reply UI state such as:

```text
Speedy is reconnecting...
Connection issue. Please try again.
```

These are status messages, not customer service replies. The widget must not display a hardcoded customer-support response such as:

```text
A support specialist will review it shortly.
We received your message and support will reply soon.
```

Customer-visible answers must come from AI.

## Handoff behavior

When backend returns `handoff_required=true`, the widget still displays the AI-generated reply immediately. Ticket creation is handled asynchronously by the backend handoff snapshot job.

The browser does not receive internal fields such as `recommended_agent_action`.

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
- Browser must never call `/v1/responses`.
- Browser bundle must not contain OpenClaw Gateway URL or bearer token.
- `data-api-base` must point to NexusDesk, not OpenClaw Gateway.
- Card/action rendering in legacy mode uses DOM text APIs and does not execute arbitrary HTML.

## Demo

```text
/webchat/demo.html
```

For production customer websites, prefer the script snippet and configure `WEBCHAT_ALLOWED_ORIGINS`.

## iframe note

The JS widget is the primary embed path. Do not globally relax `X-Frame-Options` or `frame-ancestors`. If iframe embed is later required, create a dedicated `/webchat/embed` route with isolated response headers.
