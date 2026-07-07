# WebChat WebSocket Runtime

## Rollout

The WebChat realtime runtime is additive. Existing REST APIs, `after_id` polling, handoff ownership, AI suspension, and audit rows remain authoritative.

Enable in stages:

1. `WEBCHAT_WS_ENABLED=true`
2. `WEBCHAT_WS_ADMIN_ENABLED=true`
3. `WEBCHAT_WS_PUBLIC_ENABLED=true`

`WEBCHAT_WS_BROKER=database` is the production default. WebSocket connections replay durable `webchat_events` rows and use in-process wakeups only as a latency optimization, so multiple app workers remain safe without sticky sessions.

## Public Widget Behavior

The public widget uses the durable WebChat conversation runtime:

- It creates or resumes the conversation through `/api/webchat/init`.
- It sends visitor messages to `/api/webchat/conversations/{conversation_id}/messages`.
- Once it has `conversation_id` and `visitor_token`, it opens `/api/webchat/ws`,
  sends `connection.hello` with the visitor token in the JSON body, and
  subscribes from `last_event_id`.
- It falls back to `after_id` polling when WebSocket is unavailable.

The public widget never sends `visitor_token` in a WebSocket URL. If the socket is unavailable, disabled, or closed, the widget keeps using `/api/webchat/conversations/{conversation_id}/messages?after_id=...` with `X-Webchat-Visitor-Token`.

## Rollback

Set these flags and redeploy:

```env
WEBCHAT_WS_ENABLED=false
WEBCHAT_WS_ADMIN_ENABLED=false
WEBCHAT_WS_PUBLIC_ENABLED=false
```

The agent console and public widget will fall back to the existing REST + polling paths. For a widget-only canary rollback, set `data-websocket="false"` on the embed script. For the Vite admin console, set `VITE_WEBCHAT_WS_ENABLED=false`.

## Observability

The runtime emits token-safe structured log events and Prometheus metrics for:

- `websocket_connected`
- `websocket_disconnected`
- `websocket_auth_failed`
- `websocket_event_sent`
- `websocket_event_replay`
- `websocket_fallback_polling`
- `websocket_active_connections`

Do not add `access_token`, `visitor_token`, customer message bodies, provider secrets, or raw request payloads to these log fields. Current logs use client type, reason, subscription type, event type, and event counts only.

## Connection Protection

Connection limits are enforced in-process per app worker:

```env
WEBCHAT_WS_MAX_CONNECTIONS=1000
WEBCHAT_WS_MAX_CONNECTIONS_PER_USER=10
```

`WEBCHAT_WS_MAX_CONNECTIONS` caps total WebSocket connections for a worker. `WEBCHAT_WS_MAX_CONNECTIONS_PER_USER` caps agent connections per authenticated user and visitor connections per public conversation after the visitor subscription is authenticated. In multi-worker deployments these are per-process guards; use load balancer or edge limits for global caps.

## Proxy Requirements

Nginx must forward `Upgrade` and `Connection` headers for `/api/webchat/ws`, use HTTP/1.1 upstream proxying, disable proxy buffering, and keep `proxy_read_timeout` above the server heartbeat interval.

## Validation

Minimum production validation before enabling public WebSocket:

1. Enable `WEBCHAT_WS_ENABLED=true`, `WEBCHAT_WS_ADMIN_ENABLED=true`, and `WEBCHAT_WS_PUBLIC_ENABLED=true` in staging.
2. Load the public widget, send a message that triggers AI or human handoff,
   accept the handoff in the agent console, and send an agent reply. The
   customer widget should receive the reply through a `message.created`
   WebSocket event without refresh.
3. Disable `WEBCHAT_WS_PUBLIC_ENABLED` and confirm the customer widget still
   sends messages and receives replies through polling.
4. Set `data-websocket="false"` on the embed and confirm polling fallback works
   while admin WebSocket remains enabled.
