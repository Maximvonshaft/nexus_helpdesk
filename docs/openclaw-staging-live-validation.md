# NexusDesk × OpenClaw Staging Live Validation Runbook

## Purpose

Round A mock and dry-run coverage proves local contracts only. It does not prove real OpenClaw Gateway/MCP delivery, real WhatsApp/Telegram/WebChat delivery, or real customer-thread receipt. This runbook defines the staging-only proof required before enabling customer-facing live dispatch.

## Proof levels

| Proof level | What it proves | What it does not prove |
|---|---|---|
| Mock proof | NexusDesk contract handling against deterministic fixtures | Real OpenClaw session, account, recipient, thread, or customer channel delivery |
| Dry-run proof | Script wiring, environment parsing, skip/fail semantics | Database writes or provider delivery |
| Staging DB proof | Ticket/job/outbox/transcript persistence in a disposable DB | Real customer delivery unless paired with live OpenClaw staging account |
| Real OpenClaw Gateway/MCP proof | Gateway/MCP can read/send using staging accounts | Production account correctness |
| Real customer-channel proof | Message reaches the original staging customer thread | Production customer account safety |

## Required safety switches

Do not run live validation unless all of the following are true:

```bash
export NEXUSDESK_SMOKE_MODE=staging-live
export NEXUSDESK_ALLOW_LIVE_CHANNEL_TEST=1
export NEXUSDESK_API_URL=https://<staging-api-host>
export NEXUSDESK_ADMIN_EMAIL=<staging-admin>
export NEXUSDESK_ADMIN_PASSWORD=<staging-admin-password>
export OPENCLAW_STAGING_SESSION_KEY=<known-staging-session>
export OPENCLAW_STAGING_CHANNEL=whatsapp
export OPENCLAW_STAGING_RECIPIENT=<staging-recipient>
export OPENCLAW_STAGING_ACCOUNT_ID=<staging-account-id>
export OPENCLAW_STAGING_THREAD_ID=<staging-thread-id>
```

Never point this suite at production unless a separate production change window explicitly authorizes test-data creation and test customer messaging.

## Required validation matrix

| ID | Chain | Expected result | Evidence to capture |
|---|---|---|---|
| L1 | WhatsApp inbound → ticket create/link | One and only one ticket is created or linked | ticket id, session key, route fields |
| L2 | Transcript sync | Latest staging customer message appears in ticket transcript | synced message id and body snippet |
| L3 | Attachment sync | Small staging attachment becomes an OpenClaw attachment reference and storage job | attachment ref id, storage_status |
| L4 | Ticket outbound → original thread | Reply uses original channel/account/recipient/thread | provider receipt, account_id, recipient, thread_id |
| L5 | Bridge failure | Dispatch fails closed or retries; it must not silently send through a wrong fallback | outbound status, failure_code, route context |
| L6 | Unresolved event quarantine | Ambiguous session/contact goes to unresolved queue | unresolved event id and status |
| L7 | Replay unresolved event | After operator correction, replay resolves or remains auditable | replay_count, status, last_error |
| L8 | Worker restart | Pending jobs are not lost or double-sent after restart | before/after queue counts, provider receipts |
| L9 | Route preservation | account_id, recipient, thread_id remain stable from inbound through reply | route proof log/event payload |

## Pass / fail rules

- PASS only when a real staging customer thread receives the expected reply through the original route.
- SKIP only when explicit staging/live variables are missing.
- FAIL when a required variable is present but a chain does not match expected behavior.
- Do not convert runtime failures into SKIP.

## Production gate

Production live dispatch should remain disabled or tightly controlled until:

1. all matrix rows pass in staging;
2. `OPENCLAW_CLI_FALLBACK_ENABLED=false` is confirmed for production unless separately proven route-safe;
3. outbound UI distinguishes queued, review-required, retry, dead, and sent states;
4. runtime health/signoff reports no dead worker, dead sync, dead attachment, or stale daemon blockers;
5. at least one end-to-end WhatsApp same-route reply proof is archived.
