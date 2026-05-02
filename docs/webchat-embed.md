# WebChat Embed Runbook

## Embed shape

The public widget is served from:

```html
<script
  src="https://your-helpdesk-domain.example/static/webchat/widget.js"
  data-api-base="https://your-helpdesk-domain.example"
  data-tenant="default"
  data-channel="default">
</script>
```

## Public API safety contract

- Public requests must pass origin validation through `WEBCHAT_ALLOWED_ORIGINS`.
- Production-safe visitor token transport is `X-Webchat-Visitor-Token`.
- Legacy query/body token transport must stay disabled in production.
- WebChat ACK and safe fallback are local-only and are not external provider sends.

## Runtime hardening in this branch

Backend runtime hardening adds:

- `client_message_id` support for idempotent visitor message sends.
- `after_id` and `limit` support for incremental polling.
- visitor token expiry with a 7-day default window.
- tenant-scoped rate limiting for send and poll paths.

## Widget storage note

The target storage posture is session-scoped visitor storage by default, with persistent storage only when a site explicitly opts in. The current remote patch fully implements the backend contract. The widget storage patch should be completed in a local IDE/CI environment if not already applied in this branch.

## Required verification

```bash
cd backend
pytest -q tests/test_webchat_runtime_idempotency.py
pytest -q tests/test_webchat_incremental_poll.py
pytest -q tests/test_webchat_rate_limit_tenant_scope.py
pytest -q tests/test_webchat_token_lifecycle.py
```
