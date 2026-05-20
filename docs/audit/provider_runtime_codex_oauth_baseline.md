# Provider Runtime & Codex OAuth Baseline Audit

## 1. Context
- **Date:** 2026-05-21
- **Target Branch:** main
- **Current Commit SHA:** 9b631bb89967f4d9ca61316d87c156549a788b94

## 2. File Evidence
- `backend/app/services/ai_runtime/provider_router.py`: Implements a basic router (`_provider_for`, `generate_fast_reply`) switching between hardcoded providers (`openclaw_responses`, `openai_responses`, `codex_app_server`, `codex_auth`).
- `backend/app/services/ai_runtime/codex_app_server_provider.py`: Bridges to `CODEX_APP_SERVER_BRIDGE_URL`.
- `backend/app/services/ai_runtime/codex_auth_provider.py`: Skeleton returns `codex_transport_not_confirmed` because a Codex access token cannot be used directly as an OpenAI API key.
- `backend/app/services/webchat_fast_ai_service.py`: WebChat fast lane entrypoint connecting the parser, ticket creation rules, and router.

## 3. Existing Capabilities
- Basic `FastAIProviderResult` schema with fail-closed semantics (`unavailable` states).
- Existing `WebchatFastSettings` handles `codex_enabled`, `codex_token`, `codex_app_server_canary_percent`.
- `webchat_fast_output_parser.py` already enforces strict JSON schema and ticket logic (`handoff_required`, `ticket_should_create`).
- Tools like `tools/codex-reply-bridge` already bridge to Codex App Server `/reply`.

## 4. Missing Capabilities
- True database-backed `CredentialStore` and `OAuthRefreshManager`. Currently depends on plain text ENV/file tokens (`codex_token`).
- `provider_credentials` and `provider_auth_sessions` database tables.
- End-to-end OAuth Device Code flow for Codex in the backend.
- `chatgptAuthTokens` payload builder inside the backend instead of just the bridge tools.
- Provider Rules / Routing rules stored in the database instead of hardcoded config.
- Admin UI for safe token visibility.
- Proper fallback chaining across multiple real runtime adapters (skeleton anthropic, gemini).

## 5. Risk Classification
- **Security:** High. Moving from stateless environment variables to a persisted credential store requires robust encryption and memory sanitization. Token leakage to logs or frontend is the primary risk.
- **Operational:** High. Changes to the Provider Router must not break the critical path of existing `openclaw_responses` and `openai_responses` logic in the Fast Lane.
- **Concurrency:** Medium. Multiple concurrent chats causing an OAuth token refresh could lead to `refresh_token_reused` or race conditions if not properly locked.

## 6. Construction Impact
The introduction of `ProviderRuntimeRouter` and `CredentialStore` touches the core of `WebChatFastLane`. To limit impact, the implementation will map `WEBCHAT_FAST_AI_PROVIDER=provider_runtime` to the new architecture while keeping the old values backward-compatible.

## 7. Uncertain Areas
- Can `codex_auth_profile_importer` perfectly map OpenClaw profiles without losing nested data?
- Does the Postgres lock strategy (`pg_advisory_xact_lock` vs Redis lock) conflict with existing async patterns? (Will use standard async advisory lock wrapper).
