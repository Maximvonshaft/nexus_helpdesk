# Codex Direct Health-Aware Fallback Runbook

This runbook covers the first production slice after the #406 latency baseline showed elevated Codex Direct timeouts.

## Why this exists

The baseline report showed Codex Direct timeout risk is not only latency optimization work. When Codex Direct is unhealthy, the WebChat Fast runtime needs a real AI fallback before the final rule/human fallback.

## Runtime behavior

Recommended route:

```text
codex_direct
  -> openai_responses
  -> rule_engine
```

Provider health behavior:

```text
- failover-worthy errors are recorded in memory per app process;
- once the failure threshold is reached inside the window, the provider enters cooldown;
- while in cooldown, router skips that provider and tries the next fallback;
- a later successful provider call clears that provider's local health state.
```

This first slice is intentionally in-memory only. It avoids migrations and is easy to roll back. Multi-process deployments still get per-process protection. A later PR can persist this state if needed.

## Required environment

OpenAI fallback only becomes usable when `OPENAI_API_KEY` is configured in the app container environment.

Optional tuning:

```env
OPENAI_RESPONSES_MODEL=gpt-4o-mini
OPENAI_RESPONSES_TIMEOUT_SECONDS=8
OPENAI_RESPONSES_MAX_PROMPT_CHARS=6000
OPENAI_RESPONSES_MAX_OUTPUT_TOKENS=900

PROVIDER_RUNTIME_HEALTH_FALLBACK_ENABLED=true
PROVIDER_RUNTIME_HEALTH_FAILURE_THRESHOLD=2
PROVIDER_RUNTIME_HEALTH_FAILURE_WINDOW_SECONDS=300
PROVIDER_RUNTIME_HEALTH_COOLDOWN_SECONDS=180
```

When forcing Codex Direct from env, leave fallback unset or set it explicitly:

```env
PROVIDER_RUNTIME_PRIMARY_PROVIDER=codex_direct
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=openai_responses,rule_engine
```

## Production validation

After deploying the PR and ensuring `OPENAI_API_KEY` exists, run a controlled WebChat Fast tracking scenario and inspect provider runtime audit:

```sql
SELECT created_at, provider, operation, status, error_code, elapsed_ms, safe_summary
FROM provider_runtime_audit_logs
WHERE created_at > now() - interval '30 minutes'
  AND provider IN ('codex_direct', 'openai_responses', 'rule_engine')
ORDER BY created_at DESC
LIMIT 50;
```

Expected healthy fallback evidence:

```text
codex_direct generate failed codex_direct_timeout
openai_responses generate ok
```

Expected cooldown evidence after repeated failures:

```text
codex_direct generate skipped provider_in_cooldown
openai_responses generate ok
```

## Rollback

Disable health-aware skipping while keeping the fallback adapter available:

```env
PROVIDER_RUNTIME_HEALTH_FALLBACK_ENABLED=false
```

Force legacy fallback order:

```env
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=rule_engine
```

Full rollback is reverting the PR.

## Guardrails

This change does not:

- change Speedaf API behavior;
- change tracking fact source;
- create tickets or order writes;
- bypass WebChat Fast output contract validation;
- use deterministic canned reply as the main path.
