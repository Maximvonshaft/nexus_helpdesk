# Outbound Safety

## Default production posture

NexusDesk must fail closed for customer-facing external sends.

Required default environment values:

```text
ENABLE_OUTBOUND_DISPATCH=false
OUTBOUND_PROVIDER=disabled
OPENCLAW_CLI_FALLBACK_ENABLED=false
```

## Local WebChat ACK is not external send

Rows such as the following are local WebChat runtime records only:

- `channel=web_chat`, `provider_status=webchat_delivered`
- `channel=web_chat`, `provider_status=webchat_safe_ack_delivered`
- `channel=web_chat`, `provider_status=webchat_ai_safe_fallback`

They must not count as WhatsApp, Telegram, SMS, or email provider sends.

## External send eligibility

A row can enter provider dispatch only when all conditions are true:

1. `ENABLE_OUTBOUND_DISPATCH=true`
2. `OUTBOUND_PROVIDER=openclaw`
3. channel is one of `whatsapp`, `telegram`, `sms`, `email`
4. outbound safety gate passes
5. a target or same-route session key is available

`backend/app/services/message_dispatch.py` contains a provider-level kill switch so disabled or unsupported providers cannot reach OpenClaw bridge/MCP/CLI send paths.

## Required verification

```bash
cd backend
pytest -q tests/test_outbound_message_semantics.py
pytest -q tests/test_production_dispatch_gates.py
pytest -q tests/test_outbound_semantics_single_source.py
```
