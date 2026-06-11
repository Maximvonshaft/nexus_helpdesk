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
