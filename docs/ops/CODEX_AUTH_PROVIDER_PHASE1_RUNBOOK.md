# Codex Auth Provider Phase 1 Runbook

## Purpose

Phase 1 verifies whether a future Codex-compatible provider can be attached to Nexus WebChat Fast Lane without changing the current default provider.

## Run the probe

Configure a controlled test credential source and, optionally, a test transport endpoint. Then run:

```bash
python -m app.services.ai_runtime_probe.codex_token_probe
```

## Expected safe outcomes

- Missing credential source returns a safe failure.
- Missing transport endpoint returns `transport_not_confirmed`.
- A confirmed result requires both `ok=true` and `parse_ok=true`.

## Confirm current production behavior

Production WebChat Fast Reply remains on `WEBCHAT_FAST_AI_PROVIDER=provider_runtime`. Direct provider skeletons do not send production traffic by default.

## Rollback

Keep `WEBCHAT_FAST_AI_PROVIDER=provider_runtime` and route to `openai_responses` or `rule_engine` through Provider Runtime fallback settings. Keep experimental direct providers disabled.

## Troubleshooting

- `codex_auth_not_configured`: test credential source is unavailable.
- `codex_transport_not_confirmed`: no real server-side Codex-compatible transport is configured.
- `probe_http_error`: configured probe endpoint rejected the request.
- `ai_invalid_output`: endpoint responded but did not return Fast Lane strict JSON.
