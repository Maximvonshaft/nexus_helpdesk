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

- `/health`: bridge and Gateway connection state, including `allowWrites`, `sendMessageEnabled`, `aiReplyEnabled`, and Tracking Fact lookup mode.
- `/conversations-list`: read sessions through `sessions.list`.
- `/conversation-get`: read one session by key/id from session list data.
- `/read-messages`: read transcript history through `chat.history`.
- `/poll-events`: poll captured Gateway message events.
- `/wait-events`: wait for captured Gateway message events.
- `/attachments-fetch`: inspect attachment metadata from transcript history.
- `/send-message`: send a customer-facing message through `send`; external write mode required.
- `/ai-reply`: send an internal AI prompt through `sessions.send` and then read history; controlled by the dedicated AI reply flag.
- `/tools/speedaf_lookup`: optional read-only Tracking Fact endpoint used by NexusDesk WebChat AI replies; controlled by a dedicated tracking lookup flag.

## Read-only, AI-reply, tracking lookup, and write-enabled modes

Read-only mode is safest for inbound sync and transcript inspection. It allows session/message reads and rejects customer-facing outbound writes with `bridge_writes_disabled`.

AI-reply mode is intentionally separate from external write mode. WebChat AI replies use `/ai-reply` as an internal assistant-generation path and are controlled by `OPENCLAW_BRIDGE_AI_REPLY_ENABLED`. This endpoint must not require `OPENCLAW_BRIDGE_ALLOW_WRITES=true`, because production can safely allow WebChat AI generation while keeping external channel sends disabled.

Tracking lookup mode is also separate from external write mode. `/tools/speedaf_lookup` is controlled by `OPENCLAW_BRIDGE_TRACKING_LOOKUP_ENABLED`. It calls the configured Gateway tool through `OPENCLAW_BRIDGE_TRACKING_LOOKUP_METHOD` and `OPENCLAW_BRIDGE_TRACKING_LOOKUP_TOOL_NAME`, then returns the result to NexusDesk for redaction, normalization, fact-gate evaluation, and audit. The Bridge must not log raw lookup output or full tracking numbers; logs should use request metadata and short suffixes only.

External write mode is required only for production outbound through OpenClaw `/send-message`. It must be enabled together with outbound dispatch and a Gateway scope that permits writes.

Recommended controlled-pilot posture:

```text
OPENCLAW_BRIDGE_AI_REPLY_ENABLED=true
OPENCLAW_BRIDGE_ALLOW_WRITES=false
OPENCLAW_BRIDGE_TRACKING_LOOKUP_ENABLED=false
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```

Expected behavior in that posture:

- `/ai-reply` works for WebChat AI reply generation.
- `/send-message` returns `bridge_writes_disabled`.
- `/tools/speedaf_lookup` remains disabled until the Tracking Fact MVP is explicitly enabled.
- No WhatsApp / Telegram / SMS / external provider outbound dispatch is enabled.

Tracking Fact MVP enablement overlay after tool contract and PII behavior are verified:

```text
OPENCLAW_BRIDGE_TRACKING_LOOKUP_ENABLED=true
OPENCLAW_BRIDGE_TRACKING_LOOKUP_METHOD=tools.call
OPENCLAW_BRIDGE_TRACKING_LOOKUP_TOOL_NAME=speedaf-support__speedaf_lookup
WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED=true
WEBCHAT_TRACKING_FACT_REDACTION_ENABLED=true
WEBCHAT_TRACKING_FACT_CARD_ENABLED=false
```

## Tracking Fact MVP boundary

The Tracking Fact MVP is a text-reply evidence path, not a customer-facing external send path.

```text
WebChat visitor message
→ NexusDesk extracts tracking number
→ NexusDesk calls Bridge /tools/speedaf_lookup
→ Bridge calls configured OpenClaw tool
→ NexusDesk redacts and normalizes the result
→ sanitized fact summary enters AI prompt
→ fact_evidence_present=true only when redaction and lookup succeed
→ AI text reply is stored as WebChat local delivery
```

Non-goals for Phase 1:

- no `/send-message` enablement;
- no outbound provider dispatch;
- no tracking-status card rendering;
- no raw tracking-result persistence;
- no database migration;
- no AI-generated arbitrary card JSON.

## Diagnostics

Use these checks in production:

- `openclaw gateway status`
- `openclaw status`
- `openclaw logs --follow`
- `python backend/scripts/check_openclaw_bridge_health.py`
- `python backend/scripts/wait_for_openclaw_bridge.py --timeout 20`
- `sudo systemctl status nexusdesk-openclaw-bridge.service`
- `sudo journalctl -u nexusdesk-openclaw-bridge.service -n 100 --no-pager`

The health script reports whether bridge HTTP is reachable, whether the Gateway is connected, whether customer-facing send mode is enabled, whether AI reply mode is enabled, whether tracking lookup mode is enabled, whether write scope is present, whether the Gateway runtime module exists, and the last Gateway connection/close error metadata.

## Production expectations

- Keep CLI fallback disabled in production.
- Keep `/send-message` external write mode disabled for read-only sync, WebChat AI, and Tracking Fact deployments.
- Keep `/ai-reply` enabled only when WebChat AI reply generation is intentionally active.
- Keep `/tools/speedaf_lookup` disabled until the target tool contract and PII redaction behavior are verified.
- Enable Bridge external write mode only when outbound dispatch is intentionally routed through OpenClaw.
- Do not expose sensitive runtime values, raw tracking results, full tracking numbers, or PII in API responses, logs, or frontend runtime pages.
