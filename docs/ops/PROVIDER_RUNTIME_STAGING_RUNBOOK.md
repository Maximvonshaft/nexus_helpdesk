# Provider Runtime Staging Runbook

1. Export `PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE` with a valid Fernet key.
2. Ensure `WEBCHAT_FAST_AI_PROVIDER=provider_runtime`.
3. Use the Admin API to import an ExternalChannel auth profile to test Codex integration:
   `POST /api/admin/provider-credentials/import/external_channel-auth-profile`
4. Execute `scripts/smoke/smoke_webchat_ai_runtime.sh` to verify closed-loop output schema.
