# Code X Chat Smoke Runbook

This runbook proves Nexus can use an authorized Code X credential for a real admin-only chat call. It does not mark the system production-ready; that requires a deployed nonce smoke call to pass.

Current deployment fact: the status endpoint is proven and `smoke-chat` is deployed. The previous blocker was `codex_llm_endpoint_not_configured`, which means the OAuth credential is authorized but no callable Code X LLM runtime endpoint was configured for the backend.

Current deployed state after the bridge service rollout: the `18794` `codex-app-server-bridge` process is alive, but `/readyz` returns `503 codex_app_server_real_upstream_not_configured` because no real Code X app-server upstream is running or configured on `18795`.

## Preconditions

- Admin authentication is available.
- The admin user has `runtime.manage`.
- `PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE` is mounted and readable by the backend.
- At least one active `openai-codex` OAuth credential exists for the tenant.
- Preferred existing runtime path:
  - `CODEX_APP_SERVER_BRIDGE_URL=http://codex-app-server-bridge:18794/reply`.
  - `CODEX_APP_SERVER_LOGIN_URL=http://codex-app-server-bridge:18794/login`.
  - `CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token`.
  - Optional: `CODEX_APP_SERVER_TIMEOUT_MS`.
- Direct approved LLM endpoint path:
  - `CODEX_LLM_ENDPOINT`.
  - `CODEX_LLM_API_STYLE=openai_chat` or `CODEX_LLM_API_STYLE=responses`.
  - Optional: `CODEX_LLM_MODEL`, `CODEX_LLM_TIMEOUT_SECONDS`, `CODEX_LLM_RETRIES`.
- Backward compatibility only: `CODEX_SMOKE_ENDPOINT`, `CODEX_SMOKE_MODEL`, and `CODEX_SMOKE_TIMEOUT_MS` are still accepted, but new deployments should use `CODEX_APP_SERVER_*` or `CODEX_LLM_*`.

Do not configure provider access tokens, refresh tokens, client secrets, or encryption keys inline.

The backend obtains the Code X OAuth access token through the existing provider credential encryption/decryption and `OAuthRefreshManager` path. Do not configure an OpenAI API key for this probe.

## Bridge Service

Start the production-managed bridge service:

```bash
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-app-server-bridge
```

The compose service runs `deploy/codex_app_server_bridge_proxy.py` on port `18794`, bound to the Docker bridge host address:

```bash
BIND_HOST=0.0.0.0
PORT=18794
CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token
CODEX_APP_SERVER_BRIDGE_MODE=real
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://codex-app-server-upstream:18795/reply
CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=codex_app_server
CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS=30
CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS=2
```

If the real app-server upstream is host-local instead of Docker-network reachable, set:

```bash
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://127.0.0.1:18795/reply
```

Do not point this bridge at the OpenClaw Responses proxy on `18793`; that is not accepted as Code X proof.

`/healthz` is liveness only and returns `200` when the bridge process is alive. `/readyz` is strict and checks the token file, `CODEX_APP_SERVER_BRIDGE_MODE=real`, `CODEX_APP_SERVER_REAL_UPSTREAM_URL`, and the upstream `/readyz` endpoint. It may return `503` while `/healthz` returns `200`.

If no real upstream is configured, `/readyz` and `/reply` fail closed with:

```text
codex_app_server_real_upstream_not_configured
```

If a real upstream URL is configured but not reachable or not ready, `/readyz` fails closed with:

```text
codex_app_server_real_upstream_unreachable
```

## Required 18795 Upstream

The repository contains bridge and upstream-adapter scaffolding under `tools/codex-reply-bridge/`, but there is no self-contained, fake-free Code X app-server process in this PR that can produce a real model nonce echo by itself. A real private Code X app-server upstream must be provided and bound to the bridge as:

```bash
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://codex-app-server-upstream:18795/reply
```

Required upstream contract:

- `GET /healthz`: process liveness.
- `GET /readyz`: returns HTTP `200` with ready state only when the upstream can call the real Code X app-server runtime.
- `POST /login`: accepts the refreshed Code X OAuth token or establishes the upstream session without exposing it.
- `POST /reply`: performs the actual Code X-backed LLM call and returns model text, for example `{ "reply": "..." }`.

The upstream must not require an OpenAI API key and must not use OpenClaw `18793` as proof. If this upstream is absent, the correct state is blocked, not successful.

## Bridge Probes

```bash
curl -fsS http://172.18.0.1:18794/healthz
curl -fsS http://172.18.0.1:18794/readyz
```

Expected `/readyz` before running smoke:

- HTTP `200`
- `ok=true`
- `mode=real`
- `real_upstream_configured=true`
- `reply_generation_backend=codex_app_server`
- `token_file_configured=true`

## Status Probe

```bash
curl -fsS \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://www.leakle.com/api/admin/provider-credentials/codex/status
```

Expected:

- HTTP `200`
- `secret_values_exposed=false`
- at least one credential with `status=active`

## Smoke Chat Probe

Set the Nexus backend environment and restart the backend:

```bash
CODEX_APP_SERVER_BRIDGE_URL=http://codex-app-server-bridge:18794/reply
CODEX_APP_SERVER_LOGIN_URL=http://codex-app-server-bridge:18794/login
CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token
```

```bash
NONCE="codex-smoke-$(date +%s)"
curl -fsS \
  -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  https://www.leakle.com/api/admin/provider-credentials/codex/smoke-chat \
  -d "{\"mode\":\"smoke\",\"nonce\":\"$NONCE\",\"prompt\":\"Echo the nonce exactly for Nexus runtime verification.\"}"
```

Expected success:

```json
{
  "ok": true,
  "provider": "codex",
  "credential_status": "authorized",
  "model_call_status": "completed",
  "nonce_echoed": true,
  "response_text_redacted": "...",
  "latency_ms": 1234,
  "request_id": "...",
  "warnings": []
}
```

Operational verdict after success:

```bash
SMOKE_HTTP_CODE=200
nonce_echoed=True
VERDICT=CODEX_AUTH_AND_CHAT_MODEL_CALL_CONNECTED
```

## Failure Verdicts

- `401`: authentication required.
- `403`: admin or `runtime.manage` required.
- `404 codex_credential_not_found`: no active authorized Code X credential for the tenant.
- `409 credential_refresh_required`: stored credential could not be refreshed or is expired.
- `503 codex_llm_endpoint_not_configured`: credential is authorized, but no callable `CODEX_APP_SERVER_BRIDGE_URL`, `CODEX_LLM_ENDPOINT`, or backward-compatible `CODEX_SMOKE_ENDPOINT` is configured.
- `503 codex_app_server_real_upstream_not_configured`: bridge is running but no real Code X app-server upstream is configured.
- `503 codex_app_server_real_upstream_unreachable`: bridge has an upstream URL, but the upstream `/readyz` is not reachable or not ready.
- `502 codex_provider_call_failed`: configured endpoint failed, timed out, returned invalid JSON, or returned an HTTP error.

The response must never include `access_token`, `refresh_token`, authorization headers, client secret, encryption key, or raw credential payload.

## Audit Evidence

Each invocation writes `admin_audit_logs.action=codex_smoke_chat_invoked` with safe metadata only:

- request id
- prompt hash and prompt length
- nonce hash
- credential id hash
- provider status
- model call status
- latency
- actor id

Raw prompt, raw nonce, access token, refresh token, authorization headers, and credential payloads are not stored.

## Rollback

Unset the callable endpoint/bridge and restart the backend:

```bash
unset CODEX_APP_SERVER_BRIDGE_URL
unset CODEX_APP_SERVER_LOGIN_URL
unset CODEX_LLM_ENDPOINT
unset CODEX_SMOKE_ENDPOINT
docker compose -f deploy/docker-compose.server.yml up -d --no-deps app
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server stop codex-app-server-bridge
```

After rollback, `smoke-chat` should fail closed with `503 codex_llm_endpoint_not_configured` when an authorized credential exists.
