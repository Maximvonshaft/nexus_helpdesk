# Code X Chat Smoke Runbook

This runbook proves Nexus can use an authorized Code X credential for a real admin-only chat call. It does not mark the system production-ready; that requires a deployed nonce smoke call to pass.

Current deployment fact: the status endpoint is proven and `smoke-chat` is deployed. The previous blocker was `codex_llm_endpoint_not_configured`, which means the OAuth credential is authorized but no callable Code X LLM runtime endpoint was configured for the backend.

## Preconditions

- Admin authentication is available.
- The admin user has `runtime.manage`.
- `PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE` is mounted and readable by the backend.
- At least one active `openai-codex` OAuth credential exists for the tenant.
- Preferred existing runtime path:
  - `CODEX_APP_SERVER_BRIDGE_URL`, for example the approved internal bridge `/reply` endpoint.
  - `CODEX_APP_SERVER_LOGIN_URL`, if the bridge separates OAuth login from reply calls.
  - `CODEX_APP_SERVER_TOKEN_FILE`, if the bridge requires its own shared service token.
  - Optional: `CODEX_APP_SERVER_TIMEOUT_MS`.
- Direct approved LLM endpoint path:
  - `CODEX_LLM_ENDPOINT`.
  - `CODEX_LLM_API_STYLE=openai_chat` or `CODEX_LLM_API_STYLE=responses`.
  - Optional: `CODEX_LLM_MODEL`, `CODEX_LLM_TIMEOUT_SECONDS`, `CODEX_LLM_RETRIES`.
- Backward compatibility only: `CODEX_SMOKE_ENDPOINT`, `CODEX_SMOKE_MODEL`, and `CODEX_SMOKE_TIMEOUT_MS` are still accepted, but new deployments should use `CODEX_APP_SERVER_*` or `CODEX_LLM_*`.

Do not configure provider access tokens, refresh tokens, client secrets, or encryption keys inline.

The backend obtains the Code X OAuth access token through the existing provider credential encryption/decryption and `OAuthRefreshManager` path. Do not configure an OpenAI API key for this probe.

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
docker compose -f deploy/docker-compose.server.yml up -d --no-deps backend
```

After rollback, `smoke-chat` should fail closed with `503 codex_llm_endpoint_not_configured` when an authorized credential exists.
