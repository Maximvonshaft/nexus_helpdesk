# WhatsApp Outbound Adapter Smoke Validation

## Purpose

This runbook validates the P3 WhatsApp outbound adapter closure without enabling any new customer-facing channel by default.

P3 scope:

- WhatsApp outbound uses a dedicated adapter boundary.
- The adapter validates channel, target, and active WhatsApp account before dispatch.
- The adapter calls the existing OpenClaw bridge `/send-message` contract.
- SMS, Telegram, Email remain unchanged by this phase.
- Production remains fail-closed unless runtime flags explicitly enable dispatch.

## Safety boundary

Do not run real customer sends until all of the following are true:

```text
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=openclaw
OPENCLAW_BRIDGE_ALLOW_WRITES=true
```

For controlled smoke testing, use a known internal WhatsApp target and a dedicated test ChannelAccount.

## Mock-only smoke

Run the deterministic smoke script:

```bash
cd backend
python scripts/smoke_whatsapp_outbound_adapter.py
```

Expected evidence payload:

```json
{
  "ok": true,
  "status": "sent",
  "provider_status": "sent_via_fake_whatsapp_bridge",
  "sent_at_present": true,
  "conversation_state": "waiting_customer",
  "dispatch_calls": [
    {
      "route": {
        "adapter": "whatsapp_openclaw_bridge",
        "channel": "whatsapp",
        "target": "+15550123456",
        "account_id": "wa-smoke-main"
      }
    }
  ]
}
```

## Test command

```bash
cd backend
pytest tests/test_whatsapp_outbound_adapter.py tests/test_outbound_channel_capabilities.py tests/test_outbound_message_semantics.py tests/test_production_dispatch_gates.py
```

## Real bridge smoke checklist

Use this only after P3 has merged and the bridge has a controlled test account.

1. Confirm fail-closed production config first:

```bash
printenv ENABLE_OUTBOUND_DISPATCH OUTBOUND_PROVIDER OPENCLAW_BRIDGE_ALLOW_WRITES
```

2. Configure a dedicated WhatsApp ChannelAccount in admin settings:

```text
provider=whatsapp
account_id=<test account id>
is_active=true
market_id=<optional smoke market>
```

3. Create or select a test ticket:

```text
source_channel=whatsapp
source_chat_id=<internal test WhatsApp target>
preferred_reply_channel=whatsapp
preferred_reply_contact=<internal test WhatsApp target>
```

4. Enable controlled dispatch only in staging / internal smoke:

```bash
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=openclaw
OPENCLAW_BRIDGE_ALLOW_WRITES=true
```

5. Send one low-risk test message.

6. Run one worker cycle:

```bash
cd backend
python scripts/run_worker.py --queue outbound --once
```

7. Capture evidence:

```text
outbound_channel_capabilities.json
ticket_outbound_message.json
worker_once.log
bridge_send_result.json
ticket_timeline.json
rollback_command.txt
```

## Rollback

Immediate rollback remains configuration-only:

```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OPENCLAW_BRIDGE_ALLOW_WRITES=false
```

After rollback, confirm that outbound worker processes zero messages and external dispatch is disabled.
