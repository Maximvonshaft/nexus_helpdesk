# NexusDesk Provider Runtime — Codex Provider Integration

## Purpose

This branch moves the Codex work from a probe-only experiment toward a standard NexusDesk Provider Runtime integration.

The target is not to let Codex directly operate NexusDesk. The target is to let NexusDesk treat Codex as a controlled, observable, switchable provider in the same provider-router layer as ExternalChannel and other future runtimes.

## Provider runtime target shape

```text
WebChat customer
-> /api/webchat/fast-reply
-> Nexus provider_router
-> codex_app_server provider
-> private sidecar /reply
-> private upstream adapter /reply
-> private Codex app-server reply endpoint
-> strict Fast Lane JSON
-> Nexus strict parser / safety gate
-> customer reply or fallback
```

## What this branch adds beyond the previous Codex stack

### 1. Provider capability surface

The upstream adapter now exposes a provider runtime status surface:

```text
GET /provider/status
```

The response declares the provider, runtime mode, safety level, and capabilities:

```json
{
  "provider": "codex_app_server",
  "runtime": "private_upstream_adapter",
  "mode": "codex_app_server",
  "capabilities": {
    "webchat_fast_reply": true,
    "account_login_start": true,
    "streaming": false,
    "tool_execution": false,
    "ticket_action": false,
    "handoff_decision": true
  },
  "safety_level": "reply_only"
}
```

This is the first step toward making provider capability explicit instead of hidden in environment variables and runbooks.

### 2. Nexus admin Provider Runtime status

Nexus now exposes a control-plane level status endpoint:

```text
GET /api/admin/provider-runtime/status
```

This endpoint aggregates the WebChat Fast Lane provider runtime from the Nexus side, not from a single sidecar only. It reports:

```text
configured_provider
fallback_provider
providers[].selected
providers[].configured
providers[].runtime
providers[].capabilities
providers[].controls
providers[].diagnostics
warnings
```

It is intentionally a safe status view. It does not make external network calls, send customer messages, echo secret values, or expose raw upstream payloads.

### 3. Real reply transport boundary

The previous stack deliberately stopped at:

```text
codex_app_server_reply_transport_not_implemented
```

This branch adds a configurable reply transport boundary:

```text
tools/codex-reply-bridge/upstream_reply_transport.py
```

The transport is still gated. It only runs when all of the following are true:

```text
CODEX_UPSTREAM_ADAPTER_MODE=codex_app_server
CODEX_UPSTREAM_APP_SERVER_REPLY_ENABLED=true
CODEX_UPSTREAM_APP_SERVER_BASE_URL=<private app-server base URL>
CODEX_UPSTREAM_APP_SERVER_REPLY_PATH=<relative path, default /reply>
```

### 4. Fail-closed behavior remains intact

If reply transport is disabled, the adapter returns:

```text
codex_app_server_reply_transport_disabled
```

If the upstream reply is unavailable, invalid, non-2xx, or not strict Fast Lane JSON, the adapter returns a safe error and does not pass the response through to WebChat.

### 5. Private URL and relative-path constraints remain intact

The reply transport reuses the existing private app-server URL guard.

Default behavior forbids public app-server URLs. The reply path must be relative and cannot include parent traversal segments.

### 6. No direct operational actions

This branch keeps Codex in reply-only mode.

It does not allow Codex to:

- execute shell commands;
- write files;
- scrape browser cookies;
- scrape ChatGPT sessions;
- run model-native tools;
- create or modify tickets directly;
- send customer outbound messages directly;
- perform refunds, address changes, claims, compensation, or Speedaf work-order actions.

NexusDesk remains the control plane and final policy gate.

## New environment variables

```bash
CODEX_UPSTREAM_APP_SERVER_REPLY_ENABLED=false
CODEX_UPSTREAM_APP_SERVER_REPLY_PATH=/reply
CODEX_UPSTREAM_APP_SERVER_REPLY_TOKEN_FILE=/run/nexus/codex_upstream_app_server_reply_token
# development/local only fallback:
CODEX_UPSTREAM_APP_SERVER_REPLY_TOKEN=
```

`CODEX_UPSTREAM_APP_SERVER_REPLY_ENABLED` defaults to `false` so existing Codex stack behavior remains safe after merge.

## Suggested staging validation

Run static tests:

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_provider_runtime_status.py \
  backend/tests/test_codex_upstream_reply_transport.py \
  backend/tests/test_codex_upstream_adapter_skeleton.py \
  backend/tests/test_codex_upstream_transport_boundary.py \
  backend/tests/test_codex_reply_protocol_discovery.py \
  backend/tests/test_webchat_codex_app_server_provider.py
```

Then validate Nexus and adapter status surfaces:

```bash
curl -sS http://127.0.0.1:8080/api/admin/provider-runtime/status \
  -H "Authorization: Bearer $NEXUS_ADMIN_TOKEN"

curl -sS http://127.0.0.1:18794/provider/status \
  -H "X-Nexus-Upstream-Token: $CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN"
```

Only after both status surfaces report ready provider capability should WebChat Fast Lane be pointed at the sidecar in staging.

## Rollout position

This is still a staging feature, not a production default.

Recommended rollout order:

```text
contract_fixture
-> /api/admin/provider-runtime/status ready
-> adapter /provider/status ready
-> private app-server reply endpoint selected by protocol discovery
-> reply transport enabled in staging
-> sidecar upstream mode
-> Nexus codex_app_server provider canary 1%
-> 10%
-> 50%
-> 100%
```

## Non-goals

- Replacing ExternalChannel immediately.
- Sending all customer traffic to Codex by default.
- Enabling Codex-native tools.
- Building a full agent approval runtime.
- Giving Codex direct write access to NexusDesk business objects.
