# Codex App-Server Reply Runbook

## Objective

Validate and operate the private Codex reply bridge path without changing production WebChat traffic.

The current implementation has five layers:

1. Probe: `scripts/probe_codex_app_server_reply.sh`
2. Private sidecar: `tools/codex-reply-bridge/sidecar.py`
3. Backend provider: `backend/app/services/ai_runtime/codex_app_server_provider.py`
4. Router controls: canary percent, kill switch, and low-cardinality metrics
5. Upstream adapter skeleton: `tools/codex-reply-bridge/upstream_adapter.py`

The sidecar can run in `disabled`, `stub`, or `upstream` mode. The upstream adapter can run in `disabled`, `contract_fixture`, or future `codex_app_server` mode.

## Required operator inputs

You need one of the following on the server or local dev host:

- a working private upstream Codex app-server adapter endpoint;
- a bridge shared token file for Nexus-to-sidecar authentication;
- an upstream token file if the upstream adapter requires authentication;
- the Nexus repository checkout.

Do not paste credentials into shell history. Prefer files under `/run/nexus/` with root-only permissions.

## Static tests

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_codex_app_server_reply_probe.py \
  backend/tests/test_codex_reply_bridge_sidecar.py \
  backend/tests/test_webchat_codex_app_server_provider.py \
  backend/tests/test_webchat_codex_app_server_canary_observability.py \
  backend/tests/test_codex_upstream_adapter_skeleton.py
```

## Start sidecar in local stub mode

Use this only outside production:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk

mkdir -p /run/nexus
umask 077
printf '%s' 'replace-with-local-random-token' > /run/nexus/codex_reply_bridge_shared_token

export PYTHONPATH=backend
export APP_ENV=development
export CODEX_REPLY_BRIDGE_MODE=stub
export CODEX_REPLY_BRIDGE_REQUIRE_AUTH=true
export CODEX_REPLY_BRIDGE_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_shared_token
export CODEX_REPLY_BRIDGE_HOST=127.0.0.1
export CODEX_REPLY_BRIDGE_PORT=18793

python3 tools/codex-reply-bridge/sidecar.py
```

In another shell:

```bash
export CODEX_REPLY_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_REPLY_BRIDGE_TOKEN_FILE='/run/nexus/codex_reply_bridge_shared_token'
export CODEX_REPLY_PROBE_TIMEOUT_MS='15000'

bash scripts/probe_codex_app_server_reply.sh --strict
cat artifacts/codex_reply_probe/final_verdict.txt
cat artifacts/codex_reply_probe/report.md
```

Expected result in stub mode: `PASS`.

## Upstream adapter contract fixture E2E

This validates the full local chain without using real Codex credentials:

```text
probe -> sidecar upstream mode on 18793 -> upstream adapter contract fixture on 18794 -> strict JSON
```

Run in one shell:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk
. .venv/bin/activate

mkdir -p /run/nexus
umask 077
if [ ! -s /run/nexus/codex_reply_bridge_shared_token ]; then
  python - <<'PY' > /run/nexus/codex_reply_bridge_shared_token
import secrets
print(secrets.token_urlsafe(48), end='')
PY
fi
if [ ! -s /run/nexus/codex_reply_bridge_upstream_token ]; then
  python - <<'PY' > /run/nexus/codex_reply_bridge_upstream_token
import secrets
print(secrets.token_urlsafe(48), end='')
PY
fi

export PYTHONPATH=backend
export APP_ENV=development

pkill -f 'tools/codex-reply-bridge/upstream_adapter.py' >/dev/null 2>&1 || true
pkill -f 'tools/codex-reply-bridge/sidecar.py' >/dev/null 2>&1 || true

export CODEX_UPSTREAM_ADAPTER_MODE=contract_fixture
export CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH=true
export CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_upstream_token
export CODEX_UPSTREAM_ADAPTER_HOST=127.0.0.1
export CODEX_UPSTREAM_ADAPTER_PORT=18794
python tools/codex-reply-bridge/upstream_adapter.py > /tmp/codex_upstream_adapter.log 2>&1 &
UPSTREAM_PID=$!

export CODEX_REPLY_BRIDGE_MODE=upstream
export CODEX_REPLY_BRIDGE_REQUIRE_AUTH=true
export CODEX_REPLY_BRIDGE_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_shared_token
export CODEX_REPLY_BRIDGE_UPSTREAM_URL='http://127.0.0.1:18794/reply'
export CODEX_REPLY_BRIDGE_UPSTREAM_TOKEN_FILE=/run/nexus/codex_reply_bridge_upstream_token
export CODEX_REPLY_BRIDGE_UPSTREAM_TIMEOUT_MS=15000
export CODEX_REPLY_BRIDGE_HOST=127.0.0.1
export CODEX_REPLY_BRIDGE_PORT=18793
python tools/codex-reply-bridge/sidecar.py > /tmp/codex_reply_bridge_sidecar.log 2>&1 &
SIDECAR_PID=$!

cleanup() {
  kill "$SIDECAR_PID" >/dev/null 2>&1 || true
  kill "$UPSTREAM_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18794/healthz >/dev/null 2>&1 && break
  sleep 1
done
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18793/healthz >/dev/null 2>&1 && break
  sleep 1
done

curl -fsS http://127.0.0.1:18794/readyz
curl -fsS http://127.0.0.1:18793/readyz

export CODEX_REPLY_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_REPLY_BRIDGE_TOKEN_FILE='/run/nexus/codex_reply_bridge_shared_token'
export CODEX_REPLY_PROBE_TIMEOUT_MS='15000'

bash scripts/probe_codex_app_server_reply.sh --strict
cat artifacts/codex_reply_probe/final_verdict.txt
cat artifacts/codex_reply_probe/report.md

echo '===== UPSTREAM LOG ====='
cat /tmp/codex_upstream_adapter.log

echo '===== SIDECAR LOG ====='
cat /tmp/codex_reply_bridge_sidecar.log
```

Expected result: `PASS`.

## Backend provider validation against local stub

After the sidecar is running in `stub` mode, validate that the backend provider can call it:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk
. .venv/bin/activate

export PYTHONPATH=backend
export APP_ENV=development
export WEBCHAT_FAST_AI_PROVIDER=codex_app_server
export WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=true
export WEBCHAT_FAST_AI_FALLBACK_PROVIDER=none
export CODEX_APP_SERVER_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_APP_SERVER_TOKEN_FILE='/run/nexus/codex_reply_bridge_shared_token'
export CODEX_APP_SERVER_TIMEOUT_MS='15000'
export CODEX_APP_SERVER_CANARY_PERCENT='100'
export CODEX_APP_SERVER_KILL_SWITCH='false'

python - <<'PY'
import asyncio
import json
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.services.ai_runtime.provider_router import generate_fast_reply
from app.services.ai_runtime.schemas import FastAIProviderRequest

get_webchat_fast_settings.cache_clear()
settings = get_webchat_fast_settings()

result = asyncio.run(generate_fast_reply(
    request=FastAIProviderRequest(
        tenant_key='default',
        channel_key='website',
        session_id='provider-smoke-session',
        body='Hello, where is my parcel?',
        recent_context=[],
        request_id='provider-smoke-1',
    ),
    settings=settings,
))

print(json.dumps({
    'ok': result.ok,
    'reply_source': result.reply_source,
    'raw_provider': result.raw_provider,
    'intent': result.intent,
    'reply': result.reply,
    'error_code': result.error_code,
    'safe_summary': result.raw_payload_safe_summary,
}, ensure_ascii=False, indent=2, sort_keys=True))

raise SystemExit(0 if result.ok and result.reply_source == 'codex_app_server' else 1)
PY
```

Expected result:

```json
{
  "ok": true,
  "reply_source": "codex_app_server",
  "raw_provider": "codex_app_server",
  "intent": "tracking_missing_number"
}
```

## Canary and kill switch controls

```bash
export CODEX_APP_SERVER_CANARY_PERCENT='0'
export CODEX_APP_SERVER_CANARY_PERCENT='1'
export CODEX_APP_SERVER_CANARY_PERCENT='100'
export CODEX_APP_SERVER_KILL_SWITCH='true'
```

When `WEBCHAT_FAST_AI_PROVIDER=codex_app_server`, router behavior is:

- kill switch true -> `openclaw_responses`
- canary 0 -> `openclaw_responses`
- canary 1..99 -> stable hash by tenant/session/request
- canary 100 -> `codex_app_server`

Production note: if kill switch is true or canary percent is below 100, OpenClaw production config must be valid because some or all traffic can route there.

## Future real Codex app-server mode

`CODEX_UPSTREAM_ADAPTER_MODE=codex_app_server` is intentionally not implemented in this skeleton PR. It will return:

```text
codex_app_server_transport_not_implemented
```

The future real mode must only use explicit auth sources, such as:

```text
CODEX_UPSTREAM_AUTH_PROFILE_FILE
CODEX_CLI_AUTH_FILE
CODEX_UPSTREAM_API_KEY_FILE
```

It must not scrape browser cookies or ChatGPT sessions.

## Probe-only environment variables

```bash
export CODEX_REPLY_BRIDGE_URL='http://127.0.0.1:18793/reply'
export CODEX_REPLY_BRIDGE_TOKEN_FILE='/run/nexus/codex_reply_bridge_shared_token'
export CODEX_REPLY_PROBE_TIMEOUT_MS='15000'
```

The probe also accepts `CODEX_APP_SERVER_BRIDGE_URL`, `CODEX_APP_SERVER_TOKEN_FILE`, and `CODEX_APP_SERVER_TIMEOUT_MS` aliases.

## Probe artifacts

```text
artifacts/codex_reply_probe/report.md
artifacts/codex_reply_probe/raw_sanitized.json
artifacts/codex_reply_probe/final_verdict.txt
```

## Safety validation

The probe, sidecar, backend provider, router controls, and upstream adapter skeleton enforce these rules:

- HTTP probe URLs are allowed only for loopback hosts such as `127.0.0.1` and `localhost`.
- Remote probe URLs must use HTTPS.
- URL userinfo is rejected.
- The response must pass `parse_openclaw_fast_reply`.
- Tool/function-call shaped payloads are rejected by the existing parser.
- Customer-visible internal terms are rejected by the existing parser.
- Sidecar `/reply` returns only the six strict reply fields on success.
- Backend provider returns only safe summaries, not raw upstream payloads.
- Upstream adapter `/auth/status` never echoes secrets.
- Artifact output is sanitized before writing.

## Production controls

In production:

- `CODEX_APP_SERVER_TOKEN` is forbidden.
- `CODEX_APP_SERVER_TOKEN_FILE` is required when provider is `codex_app_server`.
- `CODEX_APP_SERVER_BRIDGE_URL` must point to private, loopback, link-local, or tailnet/CGNAT address space.
- `CODEX_APP_SERVER_CANARY_PERCENT` must be 0..100.
- If `CODEX_APP_SERVER_KILL_SWITCH=true` or `CODEX_APP_SERVER_CANARY_PERCENT<100`, OpenClaw route config is required.
- Production default provider remains unchanged unless explicitly configured.

## Release gate before real upstream integration

Do not connect the sidecar to a real Codex upstream adapter until:

1. sidecar static tests pass;
2. stub mode probe returns `PASS`;
3. backend provider smoke returns `reply_source=codex_app_server`;
4. canary/kill switch tests pass;
5. upstream contract fixture probe returns `PASS`;
6. upstream real Codex mode returns `PASS` against explicit auth sources;
7. sanitized artifacts show no secret exposure.

## Rollback

Preferred rollback order:

1. Set `CODEX_APP_SERVER_KILL_SWITCH=true`.
2. Or set `CODEX_APP_SERVER_CANARY_PERCENT=0`.
3. Or set `WEBCHAT_FAST_AI_PROVIDER=openclaw_responses`.
4. Stop the sidecar/upstream processes if no longer needed.

The default provider remains unchanged unless explicitly configured.
