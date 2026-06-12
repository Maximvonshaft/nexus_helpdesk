# NexusDesk WhatsApp Sidecar

Native WhatsApp Web connector for NexusDesk. The sidecar owns WhatsApp socket lifecycle and exposes a small internal API for NexusDesk backend.

Default mode is `mock` so CI can validate the HTTP contract without a real WhatsApp session. Set `WA_SIDECAR_CONNECTOR_MODE=baileys` to use Baileys.

## Required env

- `WA_SIDECAR_INTERNAL_TOKEN`
- `NEXUS_BACKEND_URL`
- `NEXUS_CONNECTOR_KEY`
- `NEXUS_CONNECTOR_HMAC_SECRET`

## Useful env

- `WA_SIDECAR_PORT`, default `18793`
- `WHATSAPP_SESSION_ROOT`, default `/data/whatsapp-sessions`
- `WA_SIDECAR_CONNECTOR_MODE`, `mock` or `baileys`
- `NEXUS_CALLBACK_TIMEOUT_MS`, default `8000`

## Self-echo smoke mode

Production default ignores Baileys `fromMe` messages so agent replies sent by this account do not loop back as customer inbound.
For single-account UAT only, a controlled self-echo mode can be enabled:

- `WA_SIDECAR_ALLOW_FROM_ME_INBOUND`, default `false`
- `WA_SIDECAR_FROM_ME_MODE`, `ignore`, `store_only`, or `test_visitor`, default `ignore`
- `WA_SIDECAR_FROM_ME_TEST_PREFIX`, default `NEXUS_SELF_INBOUND_TEST`

`store_only` sends signed raw inbound to NexusDesk without projecting it into Unified Inbox. `test_visitor` projects only `fromMe` messages whose text starts with the configured prefix. This is a smoke-test aid, not proof of an external customer inbound path.

## API

- `GET /healthz`
- `GET /readyz`
- `POST /accounts/{account_id}/start`
- `POST /accounts/{account_id}/logout`
- `GET /accounts/{account_id}/status`
- `GET /accounts/{account_id}/qr`
- `POST /accounts/{account_id}/send`
- `POST /accounts/{account_id}/restart`

All account endpoints require `Authorization: Bearer <WA_SIDECAR_INTERNAL_TOKEN>`.
