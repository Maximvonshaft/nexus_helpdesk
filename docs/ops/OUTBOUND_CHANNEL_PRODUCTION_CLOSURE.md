# NexusDesk Outbound Channel Production Closure

## Purpose

This document defines the production boundary for NexusDesk outbound customer replies.
The objective is not to expose every enum value as a sendable customer channel. The objective is to make every customer-visible reply channel explicit, capability-gated, account-aware, target-validated, and auditable before it can appear in the agent UI or enter the external dispatch path.

## Non-negotiable principles

1. Default fail-closed: production must not send external outbound messages unless explicitly enabled.
2. No fake channels: a channel that is not ready must not appear as a customer-sendable option in the reply UI.
3. No account, no send: external channels require an active ChannelAccount.
4. No target, no send: external channels require a channel-valid target.
5. No provider, no send: external dispatch requires `ENABLE_OUTBOUND_DISPATCH=true` and `OUTBOUND_PROVIDER=native` for external messaging, or `OUTBOUND_PROVIDER=email/smtp` for email-only pilots.
6. WebChat is local delivery, not external provider dispatch.
7. Internal is not a customer-sendable outbound channel.
8. Email remains experimental until account governance, email-specific schema, and provider adapter closure are implemented.

## Channel capability matrix

| Channel | Dispatch type | Initial status | Customer-sendable | Production meaning |
| --- | --- | --- | --- | --- |
| `web_chat` | local | `local_ready` when linked conversation exists | Yes, only for WebChat tickets | Insert local WebChat reply / timeline record. Never external dispatch. |
| `whatsapp` | external | `ready` only when runtime, account, sidecar, and target gates are closed | Yes when ready | Send via native WhatsApp sidecar. |
| `sms` | external | `configurable` until a native/provider adapter is implemented | No by default | No production send path yet. |
| `telegram` | external | `configurable` until a native/provider adapter is implemented | No by default | No production send path yet. |
| `email` | external | `ready` only when runtime, SMTP account, and valid email target gates are closed | Yes when ready | Send via SMTP adapter during controlled email pilot. |
| `internal` | internal | `not_customer_sendable` | No | Use internal notes or system events instead. |

## Runtime gates

External outbound dispatch requires all of the following:

```text
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=native
WHATSAPP_DISPATCH_MODE=native_sidecar
```

The production templates intentionally default to:

```text
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```

This default remains fail-closed until channel smoke evidence is complete. There is no compatibility write switch or alternate provider path.

## API contract

The agent UI must use:

```http
GET /api/outbound/channels/capabilities
```

The reply UI must hide any channel where:

```text
customer_sendable=false
supports_send=false
status in [not_ready, experimental_not_ready, not_customer_sendable]
```

The backend send endpoint also enforces the capability registry:

```http
POST /api/tickets/{ticket_id}/outbound/send
```

If the selected channel is not sendable, the endpoint returns HTTP 400 with a structured detail payload:

```json
{
  "error_code": "outbound_channel_not_ready",
  "channel": "whatsapp",
  "status": "configurable",
  "missing": ["enable_outbound_dispatch", "whatsapp_channel_account"]
}
```

## Phase 1 scope

This branch implements the production closure foundation:

1. Outbound channel capability registry.
2. Authenticated capability API.
3. Backend send guard for `/api/tickets/{id}/outbound/send`.
4. Tests that lock the production boundary.
5. Runbook for safe rollout.

External sends use only registered native sidecars or explicit provider adapters.

## Phase 2 acceptance criteria

WhatsApp external closure is ready only when all checks pass:

1. Active WhatsApp ChannelAccount exists.
2. Ticket has valid target/session/contact.
3. Runtime gates are enabled in staging.
4. Native WhatsApp sidecar send succeeds.
5. Provider message id or stable idempotency key is persisted.
6. Success appears in ticket timeline.
7. Failure path schedules retry.
8. Max retry path marks dead.
9. Dead message can be requeued by admin.
10. Evidence pack is produced.

## Smoke evidence pack

Each production rollout must capture:

```text
outbound_channel_capabilities.json
outbound_queue_summary_before.json
outbound_queue_summary_after.json
worker_once.log
provider_send_result.json
sample_ticket_timeline.json
rollback_command.txt
```

## Rollback

Emergency rollback is configuration-only:

```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
```

After rollback, run the outbound queue summary and confirm no external delivery channel is being processed.

## Test commands

```bash
cd backend
pytest tests/test_outbound_channel_capabilities.py tests/test_outbound_message_semantics.py
```

The first test file validates the new capability boundary. The second protects the existing local-vs-external WebChat semantics.
