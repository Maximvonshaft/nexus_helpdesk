# WhatsApp Native Outbound Smoke Validation

## Purpose

This runbook validates the current WhatsApp outbound path after the legacy ExternalChannel bridge was retired.

Current scope:

- WhatsApp outbound uses the native sidecar adapter boundary.
- The adapter validates channel, target, and active WhatsApp account before dispatch.
- Production remains fail-closed unless runtime flags explicitly enable dispatch.
- The legacy ExternalChannel bridge module is absent and cannot be used for smoke evidence.

## Safety Boundary

Do not run real customer sends until all of the following are true:

```text
ENABLE_OUTBOUND_DISPATCH=true
OUTBOUND_PROVIDER=native
WHATSAPP_DISPATCH_MODE=native_sidecar
WHATSAPP_NATIVE_ENABLED=true
WHATSAPP_SIDECAR_TOKEN=<mounted secret>
```

Use a known internal WhatsApp target and a dedicated test `ChannelAccount`.

## Test Command

```bash
cd backend
pytest -q tests/test_whatsapp_native_outbound_adapter.py tests/test_whatsapp_outbound_adapter.py tests/test_outbound_channel_capabilities.py tests/test_production_dispatch_gates.py
```

## Controlled Smoke Checklist

1. Confirm fail-closed production config first:

```bash
printenv ENABLE_OUTBOUND_DISPATCH OUTBOUND_PROVIDER WHATSAPP_DISPATCH_MODE WHATSAPP_NATIVE_ENABLED
```

2. Configure a dedicated WhatsApp ChannelAccount:

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

4. Enable controlled dispatch only in staging or an internal smoke window.

5. Send one low-risk test message.

6. Run one worker cycle:

```bash
cd backend
python scripts/run_worker.py --queue outbound --once
```

7. Capture `outbound_channel_capabilities.json`, `ticket_outbound_message.json`, `worker_once.log`, sidecar response metadata, ticket timeline, and rollback command.

## Rollback

```bash
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
WHATSAPP_DISPATCH_MODE=disabled
WHATSAPP_NATIVE_ENABLED=false
```

After rollback, confirm that outbound worker processes zero messages and external dispatch is disabled.
