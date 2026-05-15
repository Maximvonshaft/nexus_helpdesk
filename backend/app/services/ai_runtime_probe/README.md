# Codex Auth Token Probe

Phase 0 verifies whether a Codex/ChatGPT authorization token can be used from
the Nexus server side to produce a Fast Lane-compatible customer-service reply.

This probe is intentionally conservative:

- It does not read `auth.json`.
- It does not scrape browser cookies or ChatGPT sessions.
- It does not expose tokens in stdout, logs, tickets, events, or frontend payloads.
- It does not assume a Codex token is a normal OpenAI API key.
- Without `CODEX_AUTH_PROBE_URL`, the result is `transport_not_confirmed`.

## Configuration

Preferred:

```bash
export CODEX_AUTH_TOKEN_FILE=/run/nexus/codex_auth_token
```

Development/test/local only:

```bash
export APP_ENV=development
export CODEX_AUTH_TOKEN='...'
```

Optional experimental transport endpoint:

```bash
export CODEX_AUTH_PROBE_URL='https://example.internal/responses'
export CODEX_AUTH_PROBE_TIMEOUT_MS=15000
```

## Run

From the backend Python environment:

```bash
python -m app.services.ai_runtime_probe.codex_token_probe
```

## Result interpretation

- `ok=true` and `parse_ok=true`: the configured probe endpoint returned output
  that passed the existing Fast Lane strict JSON parser.
- `error_code=transport_not_confirmed`: no real transport was configured; this
  is the expected safe result until a Codex-compatible server-side transport is
  selected.
- `error_code=codex_auth_token_missing`: no token source was configured.
- `error_code=production_plaintext_token_forbidden`: production attempted to use
  a plaintext `CODEX_AUTH_TOKEN` env var.

## Confirming no Tailscale/OpenClaw path was used

Do not set `OPENCLAW_RESPONSES_URL` for this probe. The probe only uses
`CODEX_AUTH_PROBE_URL` if explicitly configured and reports the safe endpoint
host/path in `raw_payload_safe_summary`.
