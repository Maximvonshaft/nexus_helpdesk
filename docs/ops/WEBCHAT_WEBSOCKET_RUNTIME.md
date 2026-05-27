# WebChat WebSocket Runtime

## Rollout

The WebChat realtime runtime is additive. Existing REST APIs, `after_id` polling, handoff ownership, AI suspension, and audit rows remain authoritative.

Enable in stages:

1. `WEBCHAT_WS_ENABLED=true`
2. `WEBCHAT_WS_ADMIN_ENABLED=true`
3. `WEBCHAT_WS_PUBLIC_ENABLED=true`

`WEBCHAT_WS_BROKER=database` is the production default. WebSocket connections replay durable `webchat_events` rows and use in-process wakeups only as a latency optimization, so multiple app workers remain safe without sticky sessions.

## Rollback

Set these flags and redeploy:

```env
WEBCHAT_WS_ENABLED=false
WEBCHAT_WS_ADMIN_ENABLED=false
WEBCHAT_WS_PUBLIC_ENABLED=false
```

The agent console and public widget will fall back to the existing REST + polling paths.

## Proxy Requirements

Nginx must forward `Upgrade` and `Connection` headers for `/api/webchat/ws`, use HTTP/1.1 upstream proxying, disable proxy buffering, and keep `proxy_read_timeout` above the server heartbeat interval.
