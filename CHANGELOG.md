# Changelog

## Unreleased
### Added
- **Multi-Provider Runtime**: Backend now acts as a Provider Runtime control plane via `ProviderRuntimeRouter`.
- **Codex OAuth Credential Custody**: Database-backed AES-encrypted storage for OAuth profiles and API keys via `CredentialCryptoService` and `OAuthRefreshManager`.
- **chatgptAuthTokens Payload Builder**: Adapter transforms credentials into bridge-compatible `chatgptAuthTokens` dynamically.
- **Strict Output Contracts**: Enforced schema-based validation for all providers to guarantee fail-closed behavior (`OutputContracts`).
- Admin Provider API skeletons for profile importing and device flow.
- Seamless compatibility mode with `WEBCHAT_FAST_AI_PROVIDER=provider_runtime`.
