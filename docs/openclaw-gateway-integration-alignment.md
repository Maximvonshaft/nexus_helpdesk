# OpenClaw Gateway Integration Alignment

This note records the NexusDesk to OpenClaw Gateway integration contract used by the bridge runtime.

Official references:

- OpenClaw CLI reference: https://docs.openclaw.ai/cli
- OpenClaw Gateway CLI: https://docs.openclaw.ai/cli/gateway
- OpenClaw Gateway runbook: https://docs.openclaw.ai/gateway/index

## Runtime boundary

OpenClaw Gateway and NexusDesk OpenClaw Bridge are separate processes.

- OpenClaw Gateway is the OpenClaw-owned WebSocket server.
- NexusDesk OpenClaw Bridge is a NexusDesk-owned HTTP service that connects to the Gateway through GatewayClient.
- NexusDesk systemd units do not start the OpenClaw Gateway. The Gateway must already be running before the bridge becomes healthy.

## Bridge contract

The bridge listens on the configured bridge URL and exposes:

- `/health`: bridge and Gateway connection state.
- `/conversations-list`: read sessions through `sessions.list`.
- `/conversation-get`: read one session by key/id from session list data.
- `/read-messages`: read transcript history through `chat.history`.
- `/poll-events`: poll captured Gateway message events.
- `/wait-events`: wait for captured Gateway message events.
- `/attachments-fetch`: inspect attachment metadata from transcript history.
- `/send-message`: send a customer-facing message through `send`; write mode required.
- `/ai-reply`: send through `sessions.send` and then read history; write mode required.

## Read-only and write-enabled modes

Read-only mode is safest for inbound sync and transcript inspection. It allows session and message reads and rejects outbound writes with `bridge_writes_disabled`.

Write-enabled mode is required for production outbound through OpenClaw. It must be enabled together with outbound dispatch and a Gateway scope that permits writes.

## Diagnostics

Use these checks in production:

- `openclaw gateway status`
- `openclaw status`
- `openclaw logs --follow`
- `python backend/scripts/check_openclaw_bridge_health.py`
- `python backend/scripts/wait_for_openclaw_bridge.py --timeout 20`
- `sudo systemctl status nexusdesk-openclaw-bridge.service`
- `sudo journalctl -u nexusdesk-openclaw-bridge.service -n 100 --no-pager`

The health script now reports whether bridge HTTP is reachable, whether the Gateway is connected, whether write mode is enabled, whether write scope is present, whether the Gateway runtime module exists, and the last Gateway connection/close error metadata.

## Production expectations

- Keep CLI fallback disabled in production.
- Keep bridge write mode disabled for read-only sync deployments.
- Enable bridge write mode only when outbound dispatch is intentionally routed through OpenClaw.
- Do not expose sensitive runtime values in API responses, logs, or frontend runtime pages.
