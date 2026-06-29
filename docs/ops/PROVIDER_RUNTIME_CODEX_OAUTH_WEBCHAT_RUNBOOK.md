# Provider Runtime Codex OAuth WebChat Runbook

## Initial Canary

Set WebChat Fast Reply to provider runtime with a 1% Codex bridge canary:

```bash
WEBCHAT_FAST_AI_PROVIDER=provider_runtime
CODEX_APP_SERVER_CANARY_PERCENT=1
CODEX_APP_SERVER_KILL_SWITCH=false
CODEX_APP_SERVER_BRIDGE_URL=http://172.18.0.1:18794/reply
CODEX_APP_SERVER_LOGIN_URL=http://172.18.0.1:18794/login
CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token
CODEX_APP_SERVER_BRIDGE_MODE=real
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://127.0.0.1:18795/reply
CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=codex_app_server
PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE=/run/nexus/provider_credential_key
```

Do not put ChatGPT/Codex OAuth access or refresh tokens in environment variables.

## Stub Smoke

`deploy/codex_app_server_bridge_proxy.py` can be started with `CODEX_APP_SERVER_BRIDGE_MODE=stub` only to verify internal bearer auth, bind address, and basic `/login` request shape. Stub mode is not production-ready and Nexus must report it as warning/not ready.

Do not use stub smoke as acceptance for customer-facing AI replies.

## Production Smoke

1. Apply migrations.
2. Start a real Codex App Server bridge on `127.0.0.1` or `172.18.0.1` with `CODEX_APP_SERVER_BRIDGE_MODE=real` and `CODEX_APP_SERVER_REAL_UPSTREAM_URL` pointing to the verified local/private runtime that generates replies from the OAuth login/session.
3. Confirm bridge `/readyz` reports:
   - `mode=real`
   - `real_upstream_configured=true`
   - `accepts_oauth_login=true`
   - `reply_generation_backend` is not `stub` or `unconfigured`
   - `token_file_configured=true`
4. Confirm `/api/admin/provider-runtime/status` reports:
   - `active_credential_exists=true`
   - `has_access=true`
   - `has_refresh=true`
   - `bridge_url_configured=true`
   - `login_url_configured=true`
   - `route_rule_exists=true`
   - `real_upstream_configured=true`
   - `bridge_mode=real`
5. Send one WebChat Fast Reply request for tenant `default`, channel `website`.
6. Verify `provider_runtime_audit_logs` has `provider=codex_app_server`, `operation=generate`, `status=ok`, and `safe_summary` bridge metadata indicates a real upstream.

## Audit Logs

Check `provider_runtime_audit_logs` for `operation=generate` rows. The `safe_summary` column must contain only metadata such as bridge status, host hash, bridge mode, real upstream configured flag, and reply generation backend. It must not contain access tokens, refresh tokens, authorization headers, customer secrets, or raw provider payloads.

## Rollback

If the canary fails, keep WebChat Fast on Provider Runtime and route to a configured fallback:

```bash
WEBCHAT_FAST_AI_PROVIDER=provider_runtime
PROVIDER_RUNTIME_PRIMARY_PROVIDER=openai_responses
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=rule_engine
CODEX_APP_SERVER_CANARY_PERCENT=0
```

Do not revoke or delete `provider_credentials` during traffic rollback. Keeping the credential allows diagnosis and a later controlled re-enable without another OAuth authorization round.
