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

- `/health`: bridge and Gateway connection state, including `allowWrites`, `sendMessageEnabled`, and `aiReplyEnabled`.
- `/conversations-list`: read sessions through `sessions.list`.
- `/conversation-get`: read one session by key/id from session list data.
- `/read-messages`: read transcript history through `chat.history`.
- `/poll-events`: poll captured Gateway message events.
- `/wait-events`: wait for captured Gateway message events.
- `/attachments-fetch`: inspect attachment metadata from transcript history.
- `/send-message`: send a customer-facing message through `send`; external write mode required.
- `/ai-reply`: send an internal AI prompt through `sessions.send` and then read history; controlled by the dedicated AI reply flag.

## Read-only, AI-reply, and write-enabled modes

Read-only mode is safest for inbound sync and transcript inspection. It allows session/message reads and rejects customer-facing outbound writes with `bridge_writes_disabled`.

AI-reply mode is intentionally separate from external write mode. WebChat AI replies use `/ai-reply` as an internal assistant-generation path and are controlled by `OPENCLAW_BRIDGE_AI_REPLY_ENABLED`. This endpoint must not require `OPENCLAW_BRIDGE_ALLOW_WRITES=true`, because production can safely allow WebChat AI generation while keeping external channel sends disabled.

External write mode is required only for production outbound through OpenClaw `/send-message`. It must be enabled together with outbound dispatch and a Gateway scope that permits writes.

Recommended controlled-pilot posture:

```text
OPENCLAW_BRIDGE_AI_REPLY_ENABLED=true
OPENCLAW_BRIDGE_ALLOW_WRITES=false
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```

Expected behavior in that posture:

- `/ai-reply` works for WebChat AI reply generation.
- `/send-message` returns `bridge_writes_disabled`.
- No WhatsApp / Telegram / SMS / external provider outbound dispatch is enabled.

## Diagnostics

Use these checks in production:

- `openclaw gateway status`
- `openclaw status`
- `openclaw logs --follow`
- `python backend/scripts/check_openclaw_bridge_health.py`
- `python backend/scripts/wait_for_openclaw_bridge.py --timeout 20`
- `sudo systemctl status nexusdesk-openclaw-bridge.service`
- `sudo journalctl -u nexusdesk-openclaw-bridge.service -n 100 --no-pager`

The health script reports whether bridge HTTP is reachable, whether the Gateway is connected, whether customer-facing send mode is enabled, whether AI reply mode is enabled, whether write scope is present, whether the Gateway runtime module exists, and the last Gateway connection/close error metadata.

## Production expectations

- Keep CLI fallback disabled in production.
- Keep `/send-message` external write mode disabled for read-only sync and WebChat AI deployments.
- Keep `/ai-reply` enabled only when WebChat AI reply generation is intentionally active.
- Enable bridge external write mode only when outbound dispatch is intentionally routed through OpenClaw.
- Do not expose sensitive runtime values in API responses, logs, or frontend runtime pages.
