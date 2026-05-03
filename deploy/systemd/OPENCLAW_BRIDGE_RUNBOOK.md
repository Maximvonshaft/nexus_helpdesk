# NexusDesk OpenClaw Bridge Runbook

## Purpose

Promote `backend/scripts/openclaw_bridge_server.js` from a manual runner into a managed service for OpenClaw read, AI-reply, and controlled outbound bridge paths.

## Service files

- `nexusdesk-openclaw-bridge.service`
- `nexusdesk-api.service`
- `nexusdesk-worker.service`

`nexusdesk-api.service` and `nexusdesk-worker.service` now declare:
- `Wants=nexusdesk-openclaw-bridge.service`
- `After=nexusdesk-openclaw-bridge.service`
- `ExecStartPre=.../wait_for_openclaw_bridge.py --timeout 20`

This means API and worker try to come up behind the bridge by default. If the bridge is disabled in env, the wait script exits cleanly and startup continues.

## Required environment

Set these in `/opt/nexusdesk/backend/.env.production` or the relevant env file.

### Required for bridge-first mode

- `OPENCLAW_BRIDGE_ENABLED=true`
- `OPENCLAW_BRIDGE_URL=http://127.0.0.1:18792`

### Bridge safety switches

- `OPENCLAW_BRIDGE_AI_REPLY_ENABLED=true`
- `OPENCLAW_BRIDGE_ALLOW_WRITES=false`

`OPENCLAW_BRIDGE_AI_REPLY_ENABLED` controls the internal `/ai-reply` WebChat AI generation path.

`OPENCLAW_BRIDGE_ALLOW_WRITES` controls the customer-facing `/send-message` path. Keep it `false` unless outbound dispatch is intentionally enabled and verified.

Recommended controlled-pilot posture:

```bash
OPENCLAW_BRIDGE_AI_REPLY_ENABLED=true
OPENCLAW_BRIDGE_ALLOW_WRITES=false
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```

Expected behavior:

- `/ai-reply` works for WebChat AI generation.
- `/send-message` returns `bridge_writes_disabled`.
- WhatsApp / Telegram / SMS external outbound dispatch remains disabled.

### Optional bridge overrides

- `OPENCLAW_BRIDGE_HOST=127.0.0.1`
- `OPENCLAW_BRIDGE_PORT=18792`
- `OPENCLAW_BRIDGE_TIMEOUT_SECONDS=20`
- `OPENCLAW_BRIDGE_REQUEST_TIMEOUT_MS=20000`
- `OPENCLAW_BRIDGE_READY_TIMEOUT_MS=8000`
- `OPENCLAW_BRIDGE_CONNECT_CHALLENGE_TIMEOUT_MS=8000`
- `OPENCLAW_BRIDGE_GATEWAY_ROLE=operator`
- `OPENCLAW_BRIDGE_GATEWAY_SCOPES=operator.read,operator.write`

### Gateway config source

Preferred non-hardcoded source:
- `OPENCLAW_CONFIG_PATH=/home/<service-user>/.openclaw/openclaw.json`

Optional explicit overrides:
- `OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789`
- `OPENCLAW_GATEWAY_TOKEN=<token>`
- `OPENCLAW_GATEWAY_RUNTIME_MODULE=/home/<service-user>/.openclaw/lib/node_modules/openclaw/dist/plugin-sdk/gateway-runtime.js`

## Secret handling

Do **not** hardcode the token in JS or service files.

Use one of these patterns:
1. Let the bridge read token from `openclaw.json`
2. Inject `OPENCLAW_GATEWAY_TOKEN` via the env file managed outside git

Health output and logs only expose `tokenSource`, never the token value.

## Node runtime

The service starts with:

```bash
/usr/bin/env node /opt/nexusdesk/backend/scripts/openclaw_bridge_server.js
```

So `node` must be on PATH for the service account. If not, either:
- install Node in a standard PATH location, or
- replace `/usr/bin/env node` with the absolute node binary path

## Install

```bash
sudo bash deploy/systemd/install_nexusdesk_bridge.sh
```

## Operations

Start:

```bash
sudo systemctl start nexusdesk-openclaw-bridge.service
```

Restart:

```bash
sudo systemctl restart nexusdesk-openclaw-bridge.service
```

Status:

```bash
sudo systemctl status nexusdesk-openclaw-bridge.service
sudo systemctl status nexusdesk-api.service
sudo systemctl status nexusdesk-worker.service
```

Logs:

```bash
sudo journalctl -u nexusdesk-openclaw-bridge.service -f
sudo journalctl -u nexusdesk-api.service -f
sudo journalctl -u nexusdesk-worker.service -f
```

Health:

```bash
python backend/scripts/check_openclaw_bridge_health.py
```

## Log interpretation

### AI reply path

Look for:
- bridge service: `action: "ai_reply"` pending request metadata
- backend: latest WebChat agent message metadata with `generated_by=webchat_ai` and `reply_source=bridge`

### Customer-facing send path

Look for:
- bridge service: `bridge_send_dispatch`, `bridge_send_success`
- backend: `openclaw_bridge_dispatch_success`

This path should be unavailable when `OPENCLAW_BRIDGE_ALLOW_WRITES=false`.

### Bridge unavailable

Look for:
- bridge service absent or `/health` disconnected
- backend: `openclaw_bridge_dispatch_failed`
- backend: `openclaw_bridge_dispatch_failed_falling_back_to_cli`

### CLI fallback path

Look for:
- backend: `openclaw_cli_fallback_invoked`
- backend: `openclaw_cli_fallback_success`

## Failure behavior

- If bridge is down, bridge-first calls fail fast or fall back according to backend service policy.
- If `OPENCLAW_CLI_FALLBACK_ENABLED=true`, outbound worker paths may fall back to `openclaw message send`.
- If fallback is disabled, outbound messages remain failed/retryable instead of silently disappearing.
- `/ai-reply` does not enable customer-facing outbound dispatch.

## Recovery behavior

Once the bridge is back and `/health` reports `gateway.connected=true`, the next bridge-backed attempts return to the main bridge path automatically. No code change is required.
