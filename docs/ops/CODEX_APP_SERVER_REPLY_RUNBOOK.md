# Codex App-Server Reply Runbook

## Objective

Validate and operate the private Codex reply bridge path without changing production WebChat traffic.

The current implementation has nine layers:

1. Probe: `scripts/probe_codex_app_server_reply.sh`
2. Private sidecar: `tools/codex-reply-bridge/sidecar.py`
3. Backend provider: `backend/app/services/ai_runtime/codex_app_server_provider.py`
4. Router controls: canary percent, kill switch, and low-cardinality metrics
5. Upstream adapter skeleton: `tools/codex-reply-bridge/upstream_adapter.py`
6. Upstream auth discovery: `tools/codex-reply-bridge/upstream_auth_discovery.py`
7. Login payload boundary: `tools/codex-reply-bridge/upstream_login_payload_boundary.py`
8. Transport boundary: `tools/codex-reply-bridge/upstream_transport_boundary.py`
9. Local app-server contract fixture: `tools/codex-reply-bridge/app_server_contract_fixture.py`

The sidecar can run in `disabled`, `stub`, or `upstream` mode. The upstream adapter can run in `disabled`, `contract_fixture`, or `codex_app_server` mode.

## Required operator inputs

You need:

- a bridge shared token file for Nexus-to-sidecar authentication;
- an upstream token file if the upstream adapter requires authentication;
- an explicit Codex auth source file for future real mode;
- the Nexus repository checkout.

For real OpenClaw/Codex integration, use only an explicit local auth source and a private app-server endpoint. Do not scrape browser cookies or ChatGPT sessions.

Do not paste credentials into shell history. Prefer files under `/run/nexus/` with root-only permissions.

## Static tests

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_codex_app_server_reply_probe.py \
  backend/tests/test_codex_reply_bridge_sidecar.py \
  backend/tests/test_webchat_codex_app_server_provider.py \
  backend/tests/test_webchat_codex_app_server_canary_observability.py \
  backend/tests/test_codex_upstream_adapter_skeleton.py \
  backend/tests/test_codex_upstream_auth_discovery.py \
  backend/tests/test_codex_upstream_login_payload_boundary.py \
  backend/tests/test_codex_upstream_transport_boundary.py \
  backend/tests/test_codex_app_server_contract_fixture.py
```

## Login payload boundary

OpenClaw's Codex app-server auth bridge sends `account/login/start` with one of these payload shapes:

```json
{ "type": "chatgptAuthTokens", "accessToken": "...", "chatgptAccountId": "...", "chatgptPlanType": "..." }
```

or:

```json
{ "type": "apiKey", "apiKey": "..." }
```

The local boundary module constructs this internal payload from explicit auth sources, but `/auth/status` exposes only a safe summary:

```json
{
  "source_kind": "auth_profile_file",
  "login_type": "chatgptAuthTokens",
  "payload_ready": true,
  "secret_fingerprint": "sha256:...",
  "chatgpt_account_id_present": true,
  "chatgpt_plan_type_present": true,
  "error_code": null
}
```

No access token or API key is returned in `/auth/status`.

## Transport boundary

The transport boundary is the first layer that can call a private app-server endpoint. By default it is dry-run only:

```text
CODEX_UPSTREAM_APP_SERVER_LOGIN_DRY_RUN=true
```

Supported controls:

```text
CODEX_UPSTREAM_APP_SERVER_BASE_URL=http://127.0.0.1:18795
CODEX_UPSTREAM_APP_SERVER_TIMEOUT_MS=15000
CODEX_UPSTREAM_APP_SERVER_ALLOW_PUBLIC_URL=false
CODEX_UPSTREAM_APP_SERVER_LOGIN_DRY_RUN=true
```

Rules:

- Public URLs are rejected unless explicitly allowed.
- Plain HTTP is only accepted for loopback hosts.
- URL userinfo is rejected.
- Timeout is clamped between 500 ms and 30000 ms.
- `/transport/login-start` returns dry-run output by default and does not send `account/login/start`.
- When dry-run is false, it posts the internal login payload to `account/login/start` on the configured private base URL.
- Transport status and result summaries never echo credential material.
- Reply generation transport remains explicitly not implemented in this PR line.

## Local app-server contract fixture

The local fixture implements only:

```text
GET  /healthz
POST /account/login/start
```

It validates the `account/login/start` payload shape and returns a sanitized success response. It does not implement reply generation, model calls, shell execution, browser access, session scraping, or tool execution.

Use it to prove the non-dry-run HTTP path before connecting to any real OpenClaw/Codex endpoint.

## Synthetic transport dry-run validation

This validates readiness without sending `account/login/start`:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk
. .venv/bin/activate

mkdir -p /tmp/codex_transport_demo
cat > /tmp/codex_transport_demo/auth_profile.json <<'JSON'
{
  "profiles": {
    "openai-codex:default": {
      "type": "token",
      "provider": "openai-codex",
      "access": "synthetic-access-token-for-transport-test-only",
      "accountId": "acct_demo",
      "chatgptPlanType": "plus"
    }
  }
}
JSON

mkdir -p /run/nexus
umask 077
if [ ! -s /run/nexus/codex_reply_bridge_upstream_token ]; then
  python - <<'PY' > /run/nexus/codex_reply_bridge_upstream_token
import secrets
print(secrets.token_urlsafe(48), end='')
PY
fi

export PYTHONPATH=backend
export APP_ENV=development
export CODEX_UPSTREAM_ADAPTER_MODE=codex_app_server
export CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH=true
export CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_upstream_token
export CODEX_UPSTREAM_AUTH_PROFILE_FILE=/tmp/codex_transport_demo/auth_profile.json
export CODEX_UPSTREAM_APP_SERVER_BASE_URL=http://127.0.0.1:18795
export CODEX_UPSTREAM_APP_SERVER_LOGIN_DRY_RUN=true
export CODEX_UPSTREAM_ADAPTER_HOST=127.0.0.1
export CODEX_UPSTREAM_ADAPTER_PORT=18794

pkill -f 'tools/codex-reply-bridge/upstream_adapter.py' >/dev/null 2>&1 || true
python tools/codex-reply-bridge/upstream_adapter.py > /tmp/codex_upstream_adapter.log 2>&1 &
UPSTREAM_PID=$!
trap 'kill "$UPSTREAM_PID" >/dev/null 2>&1 || true' EXIT

for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18794/healthz >/dev/null 2>&1 && break
  sleep 1
done

TOKEN="$(cat /run/nexus/codex_reply_bridge_upstream_token)"

curl -fsS -H "X-Nexus-Upstream-Token: $TOKEN" \
  http://127.0.0.1:18794/transport/status

curl -fsS -X POST -H "X-Nexus-Upstream-Token: $TOKEN" \
  http://127.0.0.1:18794/transport/login-start
```

Expected dry-run fields:

```json
{
  "ok": true,
  "dry_run": true,
  "login_payload_boundary": {
    "payload_ready": true,
    "login_type": "chatgptAuthTokens"
  },
  "transport_boundary": {
    "base_url_accepted": true,
    "account_login_start_request": false,
    "external_network_call": false
  }
}
```

The synthetic value must not appear in output.

## Local non-dry-run fixture validation

This validates the real HTTP path against the local contract fixture only:

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk
. .venv/bin/activate

mkdir -p /tmp/codex_fixture_demo
cat > /tmp/codex_fixture_demo/auth_profile.json <<'JSON'
{
  "profiles": {
    "openai-codex:default": {
      "type": "token",
      "provider": "openai-codex",
      "access": "synthetic-access-token-for-fixture-test-only",
      "accountId": "acct_demo",
      "chatgptPlanType": "plus"
    }
  }
}
JSON

mkdir -p /run/nexus
umask 077
if [ ! -s /run/nexus/codex_reply_bridge_upstream_token ]; then
  python - <<'PY' > /run/nexus/codex_reply_bridge_upstream_token
import secrets
print(secrets.token_urlsafe(48), end='')
PY
fi

export PYTHONPATH=backend
export APP_ENV=development

pkill -f 'tools/codex-reply-bridge/app_server_contract_fixture.py' >/dev/null 2>&1 || true
pkill -f 'tools/codex-reply-bridge/upstream_adapter.py' >/dev/null 2>&1 || true

export CODEX_CONTRACT_FIXTURE_HOST=127.0.0.1
export CODEX_CONTRACT_FIXTURE_PORT=18795
python tools/codex-reply-bridge/app_server_contract_fixture.py > /tmp/codex_app_server_contract_fixture.log 2>&1 &
FIXTURE_PID=$!

export CODEX_UPSTREAM_ADAPTER_MODE=codex_app_server
export CODEX_UPSTREAM_ADAPTER_REQUIRE_AUTH=true
export CODEX_UPSTREAM_ADAPTER_SHARED_TOKEN_FILE=/run/nexus/codex_reply_bridge_upstream_token
export CODEX_UPSTREAM_AUTH_PROFILE_FILE=/tmp/codex_fixture_demo/auth_profile.json
export CODEX_UPSTREAM_APP_SERVER_BASE_URL=http://127.0.0.1:18795
export CODEX_UPSTREAM_APP_SERVER_LOGIN_DRY_RUN=false
export CODEX_UPSTREAM_ADAPTER_HOST=127.0.0.1
export CODEX_UPSTREAM_ADAPTER_PORT=18794
python tools/codex-reply-bridge/upstream_adapter.py > /tmp/codex_upstream_adapter.log 2>&1 &
UPSTREAM_PID=$!

cleanup() {
  kill "$UPSTREAM_PID" >/dev/null 2>&1 || true
  kill "$FIXTURE_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18795/healthz >/dev/null 2>&1 && break
  sleep 1
done
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18794/healthz >/dev/null 2>&1 && break
  sleep 1
done

TOKEN="$(cat /run/nexus/codex_reply_bridge_upstream_token)"

curl -fsS -H "X-Nexus-Upstream-Token: $TOKEN" \
  http://127.0.0.1:18794/transport/status

curl -fsS -X POST -H "X-Nexus-Upstream-Token: $TOKEN" \
  http://127.0.0.1:18794/transport/login-start

{
  curl -fsS -H "X-Nexus-Upstream-Token: $TOKEN" \
    http://127.0.0.1:18794/transport/status
  curl -fsS -X POST -H "X-Nexus-Upstream-Token: $TOKEN" \
    http://127.0.0.1:18794/transport/login-start
} | grep -q 'synthetic-access-token-for-fixture-test-only' \
  && { echo 'SECRET_LEAK_FAIL'; exit 1; } \
  || echo 'SECRET_LEAK_PASS'

echo '===== FIXTURE LOG ====='
cat /tmp/codex_app_server_contract_fixture.log

echo '===== UPSTREAM LOG ====='
cat /tmp/codex_upstream_adapter.log
```

Expected non-dry-run fields:

```json
{
  "ok": true,
  "dry_run": false,
  "transport": {
    "ok": true,
    "endpoint_path": "account/login/start",
    "status_code": 200,
    "response_keys": ["account", "capabilities", "ok", "sessionId"]
  }
}
```

Expected leak check:

```text
SECRET_LEAK_PASS
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

This validates the full local reply chain without using real Codex credentials:

```text
probe -> sidecar upstream mode on 18793 -> upstream adapter contract fixture on 18794 -> strict JSON
```

Expected result: `PASS`.

## Backend provider validation against local stub

After the sidecar is running in `stub` mode, validate that the backend provider can call it and return `reply_source=codex_app_server`.

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

Production note: if kill switch is true or canary percent is below 100, OpenClaw route config is required because some or all traffic can route there.

## Current real Codex app-server mode

`CODEX_UPSTREAM_ADAPTER_MODE=codex_app_server` currently performs:

- auth discovery;
- login payload boundary construction;
- private app-server URL validation;
- dry-run `account/login/start` boundary;
- optional real `account/login/start` call when dry-run is disabled.

It still returns this for customer reply generation:

```text
codex_app_server_reply_transport_not_implemented
```

The next real mode must implement reply/turn transport only after the private app-server response protocol is confirmed.

It must not scrape browser cookies or ChatGPT sessions.

## Probe artifacts

```text
artifacts/codex_reply_probe/report.md
artifacts/codex_reply_probe/raw_sanitized.json
artifacts/codex_reply_probe/final_verdict.txt
```

## Safety validation

The probe, sidecar, backend provider, router controls, upstream adapter skeleton, auth discovery, login payload boundary, transport boundary, and local app-server contract fixture enforce these rules:

- HTTP probe URLs are allowed only for loopback hosts such as `127.0.0.1` and `localhost`.
- Remote probe URLs must use HTTPS.
- URL userinfo is rejected.
- The response must pass `parse_openclaw_fast_reply`.
- Tool/function-call shaped payloads are rejected by the existing parser.
- Customer-visible internal terms are rejected by the existing parser.
- Sidecar `/reply` returns only the six strict reply fields on success.
- Backend provider returns only safe summaries, not raw upstream payloads.
- Upstream adapter `/auth/status` never echoes secrets.
- Auth discovery returns only source kind, credential kind, login type, hints, and fingerprint.
- Login payload boundary returns only source kind, login type, readiness, hints, and fingerprint in status output.
- Transport boundary returns only endpoint path, status, response key names, and error code.
- Local app-server contract fixture returns only session/capability metadata and credential fingerprints.
- Artifact output is sanitized before writing.

## Production controls

In production:

- `CODEX_APP_SERVER_TOKEN` is forbidden.
- `CODEX_APP_SERVER_TOKEN_FILE` is required when provider is `codex_app_server`.
- `CODEX_APP_SERVER_BRIDGE_URL` must point to private, loopback, link-local, or tailnet/CGNAT address space.
- `CODEX_APP_SERVER_CANARY_PERCENT` must be 0..100.
- If `CODEX_APP_SERVER_KILL_SWITCH=true` or `CODEX_APP_SERVER_CANARY_PERCENT<100`, OpenClaw route config is required.
- Production default provider remains unchanged unless explicitly configured.

## Release gate before real upstream reply integration

Do not connect the sidecar to a real Codex reply/turn transport until:

1. sidecar static tests pass;
2. stub mode probe returns `PASS`;
3. backend provider smoke returns `reply_source=codex_app_server`;
4. canary/kill switch tests pass;
5. upstream contract fixture probe returns `PASS`;
6. auth discovery tests pass and `/auth/status` shows no credential exposure;
7. login payload boundary tests pass and `/auth/status` shows no credential exposure;
8. transport boundary tests pass;
9. dry-run `/transport/login-start` shows no credential exposure;
10. local non-dry-run fixture `/transport/login-start` returns a sanitized success summary;
11. private reply/turn protocol is confirmed;
12. sanitized artifacts show no credential exposure.

## Rollback

Preferred rollback order:

1. Set `CODEX_APP_SERVER_KILL_SWITCH=true`.
2. Or set `CODEX_APP_SERVER_CANARY_PERCENT=0`.
3. Or set `WEBCHAT_FAST_AI_PROVIDER=openclaw_responses`.
4. Stop the sidecar/upstream/fixture processes if no longer needed.

The default provider remains unchanged unless explicitly configured.
