# Codex OAuth Credential Custody

NexusDesk does not store plain text OpenAI/Codex OAuth tokens in configurations anymore.
Tokens are persisted in `provider_credentials` with AES encryption (Fernet) via `CredentialCryptoService`.

## Security Policies
1. **Never print tokens in logs.**
2. **Never expose tokens in API responses.**
3. **Never send tokens to frontend.**
4. **Never save tokens in Support Tickets.**
5. Production requires `PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE`. Fallback to plain ENV is rejected in production.

## Refresh Flow
`OAuthRefreshManager` handles access token expiration.
It uses an application-level `asyncio.Lock()` + a Postgres `pg_advisory_xact_lock` to prevent the "thundering herd" problem and avoid reusing the refresh token simultaneously across multiple workers.
