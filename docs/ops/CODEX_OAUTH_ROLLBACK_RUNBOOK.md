# Codex OAuth Rollback Runbook

If the Provider Runtime causes latency or parsing failures in production:
1. Set `WEBCHAT_FAST_AI_PROVIDER=openclaw_responses` to fallback to the legacy hardcoded path.
2. Restart the Nexus backend container.
3. The system will bypass the `ProviderRuntimeRouter` and restore previous behavior.
