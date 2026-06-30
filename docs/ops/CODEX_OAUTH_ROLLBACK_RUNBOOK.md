# Codex OAuth Rollback Runbook

If Provider Runtime causes latency or parsing failures in production, do not bypass it with retired direct providers.

1. Keep `WEBCHAT_FAST_AI_PROVIDER=provider_runtime`.
2. Set Provider Runtime routing to a known fallback:

```bash
PROVIDER_RUNTIME_PRIMARY_PROVIDER=openai_responses
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=rule_engine
PROVIDER_RUNTIME_KILL_SWITCH=false
```

3. Restart the Nexus backend container and confirm `/api/admin/provider-runtime/status`.
4. Leave `provider_credentials` intact for diagnosis and future controlled re-enable.
