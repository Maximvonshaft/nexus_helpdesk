# Codex Device Flow OpenClaw Compatibility

This note documents the NexusDesk compatibility layer for OpenClaw-style Codex device authorization.

## Why this exists

OpenClaw's Codex device authorization flow is not identical to the generic OAuth device-code shape that many providers use.

Generic shape usually polls with:

```json
{
  "client_id": "...",
  "device_code": "..."
}
```

OpenClaw/Codex polls with:

```json
{
  "device_auth_id": "...",
  "user_code": "..."
}
```

OpenClaw also treats `403` and `404` from the device-token poll endpoint as an authorization-pending state, then exchanges the returned authorization code with the Codex device callback redirect URI.

NexusDesk now supports both shapes.

## Environment controls

```bash
CODEX_OAUTH_DEVICE_POLL_PAYLOAD_MODE=auto
```

Allowed values:

- `auto`: default. Uses OpenClaw-compatible polling when the configured token path contains `deviceauth/token` or the auth base host is `auth.openai.com`; otherwise uses the legacy generic payload.
- `openclaw`: force OpenClaw/Codex polling payload: `device_auth_id + user_code`.
- `generic_device_code`: force legacy generic polling payload: `client_id + device_code`.

Optional override:

```bash
CODEX_OAUTH_DEVICE_REDIRECT_URI=https://auth.openai.com/deviceauth/callback
```

If this is unset and the selected poll mode is OpenClaw-compatible, NexusDesk derives it from:

```text
${CODEX_OAUTH_AUTH_BASE_URL}/deviceauth/callback
```

## Recommended OpenClaw/Codex settings

```bash
CODEX_OAUTH_DEVICE_FLOW_ENABLED=true
CODEX_OAUTH_AUTH_BASE_URL=https://auth.openai.com
CODEX_OAUTH_CLIENT_ID=app_EMoamEEZ73f0CkXaXp7hrann
CODEX_OAUTH_DEVICE_USERCODE_PATH=/api/accounts/deviceauth/usercode
CODEX_OAUTH_DEVICE_TOKEN_PATH=/api/accounts/deviceauth/token
CODEX_OAUTH_TOKEN_PATH=/oauth/token
CODEX_OAUTH_DEVICE_POLL_PAYLOAD_MODE=auto
```

## Safety boundary

- The backend never returns raw access or refresh tokens to the frontend.
- Successful authorization is stored in `provider_credentials` using encrypted token fields.
- `provider_auth_sessions` stores session and device-flow metadata only.
- Production deployments must still use `PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE` and must not place real tokens in source control or logs.
