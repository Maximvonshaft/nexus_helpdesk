# NexusDesk Webchat Widget

## What it is

Round B adds a first production-shaped Webchat entry point for customer websites. A website embeds one JavaScript snippet, visitors send messages, NexusDesk creates or links a Webchat ticket, agents reply from the authenticated admin UI, and the visitor sees the reply in the same widget.

## Embed snippet

```html
<script src="https://YOUR_NEXUSDESK_DOMAIN/webchat/widget.js" data-tenant="default" data-channel="website" async></script>
```

Optional attributes:

```html
<script
  src="https://YOUR_NEXUSDESK_DOMAIN/webchat/widget.js"
  data-tenant="speedaf-ch"
  data-channel="website-zurich"
  data-title="SpeedAF Support"
  data-welcome="Hi, how can we help you today?"
  async>
</script>
```

## API flow

1. Widget calls `POST /api/webchat/init`.
2. Backend creates `webchat_conversations`, a NexusDesk ticket, and a visitor token.
3. Widget calls `POST /api/webchat/conversations/{conversation_id}/messages` to send visitor messages.
4. Widget polls `GET /api/webchat/conversations/{conversation_id}/messages?visitor_token=...`.
5. Admin UI calls `GET /api/webchat/admin/conversations` and `GET /api/webchat/admin/tickets/{ticket_id}/thread`.
6. Agent replies through `POST /api/webchat/admin/tickets/{ticket_id}/reply`.
7. Reply is written to `webchat_messages`, `ticket_comments`, and `ticket_outbound_messages` with `webchat_delivered` status.

## Safety design

- Public API never exposes internal numeric ticket ids to visitors.
- Visitor reads require `conversation_id + visitor_token`.
- Empty and oversized messages are rejected.
- Basic in-memory rate limiting protects public init/send routes.
- Agent replies pass through `evaluate_outbound_safety`.
- Sensitive terms such as `SECRET_KEY`, `password`, `token`, and `stack trace` are blocked.
- Logistics factual commitments require evidence or human review confirmation.

## Local demo

After applying the patch and running the app:

```bash
open http://127.0.0.1:18081/webchat/demo.html
```

Then open the authenticated admin UI and go to `/webchat`.

## Smoke

```bash
BASE_URL=http://127.0.0.1:18081 NEXUSDESK_DEV_USER_ID=1 bash scripts/smoke/smoke_webchat_round_b.sh
```

For production-like auth:

```bash
BASE_URL=https://YOUR_DOMAIN NEXUSDESK_ADMIN_TOKEN='<jwt>' bash scripts/smoke/smoke_webchat_round_b.sh
```

## Current Round B limits

- Polling is used instead of WebSocket/SSE for stability.
- Tenant origin allowlist is not yet persisted; public CORS currently echoes the caller origin for widget API routes.
- Webchat replies are delivered into the widget itself; no external WhatsApp/Email/OpenClaw dispatch is performed in Round B.
- Round C should add configured widget channels, origin allowlists, OpenClaw suggested replies, and real-time push.
