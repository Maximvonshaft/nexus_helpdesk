# NexusDesk WebChat Widget

## What it is

NexusDesk WebChat is the public customer-side support runtime for customer websites. A website embeds one JavaScript snippet, visitors send messages or click structured actions, NexusDesk creates or links a WebChat ticket, agents reply from the authenticated admin UI, and the visitor sees replies in the same widget.

The visitor entry point and the admin console are intentionally separate:

- Visitor: public widget or `/webchat/demo.html`, no login.
- Admin: `/webchat` inside the authenticated NexusDesk console.

## Basic embed

```html
<script src="https://YOUR_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

## Optional attributes

```html
<script
  src="https://YOUR_DOMAIN/webchat/widget.js"
  data-tenant="speedaf-ch"
  data-channel="website-zurich"
  data-title="Speedaf Support"
  data-subtitle="Secure website support"
  data-assistant-name="Speedy"
  data-locale="en"
  data-welcome="Hi, how can we help you today?"
  async>
</script>
```

Supported attributes:

- `data-tenant`: tenant key, default `default`.
- `data-channel`: channel key, default `default`.
- `data-title`: widget header title.
- `data-subtitle`: widget header subtitle.
- `data-assistant-name`: public assistant name.
- `data-locale`: locale hint.
- `data-welcome`: local welcome text before conversation creation.
- `data-api-base`: API origin override for controlled staging/demo use.

No admin token, OpenClaw token, bridge credential, MCP credential, or internal API secret is stored in the browser.

## API flow

1. Widget calls `POST /api/webchat/init`.
2. Backend creates or resumes `webchat_conversations` and a NexusDesk ticket.
3. Widget sends visitor messages to `POST /api/webchat/conversations/{conversation_id}/messages`.
4. Widget polls `GET /api/webchat/conversations/{conversation_id}/messages?after_id=...&limit=...`.
5. Widget submits card actions to `POST /api/webchat/conversations/{conversation_id}/actions`.
6. Admin UI reads `/api/webchat/admin/conversations` and `/api/webchat/admin/tickets/{ticket_id}/thread`.
7. Agent replies through `/api/webchat/admin/tickets/{ticket_id}/reply`.

## Structured cards

This release renders:

- `quick_replies`
- `handoff`

Unknown card types degrade to a safe text fallback. Card text is rendered through DOM `textContent`; the widget does not execute HTML, JS, CSS, iframe, or arbitrary markup from cards.

## Runtime behavior

- Uses `sessionStorage` for conversation id and visitor token.
- Uses `client_message_id` for optimistic send idempotency.
- Uses stable client ids for retry, so a failed send can be retried without creating a duplicate visitor message.
- Uses incremental polling with `after_id` after the first full history load.
- Uses request timeout and exponential backoff.
- Pauses/slows polling when the page is hidden.
- Avoids full DOM redraw and appends new messages/cards.
- Shows sending / failed states.
- Shows a Retry button for failed optimistic visitor messages.
- Disables card action buttons while submitting.

## Action idempotency

Card action submission is protected in three layers:

1. Widget disables the clicked action button during submit.
2. API returns the existing action response for a repeated same card/action submit.
3. Database schema enforces one visitor submission per `(conversation_id, message_id, action_id, submitted_by)`.

This prevents double-clicks and normal network retries from duplicating `webchat_card_actions`, action messages, and ticket comments.

## Demo

```text
/webchat/demo.html
```

For production customer websites, prefer the script snippet and configure `WEBCHAT_ALLOWED_ORIGINS`.

## iframe note

The JS widget is the primary embed path. Do not globally relax `X-Frame-Options` or `frame-ancestors`. If iframe embed is later required, create a dedicated `/webchat/embed` route with isolated response headers.
