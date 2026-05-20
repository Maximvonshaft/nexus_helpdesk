# Codex Reply Protocol Discovery Gate

## Purpose

This gate discovers the private app-server reply/turn protocol before any customer-visible Codex reply transport is implemented.

It does not authenticate to ChatGPT, scrape browser sessions, execute shell commands, call tools, or send real customer messages.

## Tool

```text
tools/codex-reply-bridge/reply_protocol_discovery.py
```

## Default behavior

By default the tool only probes candidate paths with:

```text
OPTIONS
GET
```

It does not send POST unless explicitly enabled.

Default candidate paths:

```text
/healthz
/readyz
/openapi.json
/docs
/account/status
/conversation
/conversation/start
/conversation/turn
/conversation/reply
/reply
/turn
/chat
/messages
/responses
```

## Safety rules

- Base URL must pass the private app-server URL gate.
- Plain HTTP is accepted only for loopback hosts.
- Public URLs are rejected unless explicitly allowed.
- URL userinfo is rejected.
- Probe output includes only status code, content type, allow header, response key names, and body byte length.
- Response body is not copied into output.
- POST probe body is synthetic and marked as a probe.
- No credential material is sent.
- No real customer message is sent.

## Static test

```bash
PYTHONPATH=backend pytest -q \
  backend/tests/test_codex_reply_protocol_discovery.py
```

Full Codex stack test set:

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
  backend/tests/test_codex_app_server_contract_fixture.py \
  backend/tests/test_codex_reply_protocol_discovery.py
```

## Safe OPTIONS/GET discovery against local fixture

```bash
set -Eeuo pipefail

cd /opt/nexus_helpdesk || cd ~/nexus_helpdesk
. .venv/bin/activate

export PYTHONPATH=backend

pkill -f 'tools/codex-reply-bridge/app_server_contract_fixture.py' >/dev/null 2>&1 || true

export CODEX_CONTRACT_FIXTURE_HOST=127.0.0.1
export CODEX_CONTRACT_FIXTURE_PORT=18795
python tools/codex-reply-bridge/app_server_contract_fixture.py > /tmp/codex_app_server_contract_fixture.log 2>&1 &
FIXTURE_PID=$!
trap 'kill "$FIXTURE_PID" >/dev/null 2>&1 || true' EXIT

for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18795/healthz >/dev/null 2>&1 && break
  sleep 1
done

python tools/codex-reply-bridge/reply_protocol_discovery.py \
  --base-url http://127.0.0.1:18795 \
  --candidate-paths '/healthz,/account/login/start,/reply,/turn,/conversation/turn'

echo '===== FIXTURE LOG ====='
cat /tmp/codex_app_server_contract_fixture.log
```

Expected boundary fields:

```json
{
  "credential_material_sent": false,
  "customer_message_sent": false,
  "post_probe_enabled": false,
  "browser_cookie_scraping": false,
  "chatgpt_session_scraping": false,
  "shell_execution": false,
  "tool_execution": false
}
```

## Explicit synthetic POST discovery

Use this only against local/private endpoints that are safe to receive synthetic probe messages.

```bash
python tools/codex-reply-bridge/reply_protocol_discovery.py \
  --base-url http://127.0.0.1:18795 \
  --candidate-paths '/account/login/start,/reply,/turn,/conversation/turn' \
  --allow-post-probe
```

The POST body is synthetic:

```json
{
  "probe": true,
  "request_id": "protocol-discovery-probe",
  "session_id": "protocol-discovery-session",
  "body": "Synthetic protocol discovery probe. Do not treat as a customer message."
}
```

The synthetic body text is not copied into the output.

## Interpretation

The discovery result is not a production integration result. It is only evidence for selecting the next real reply/turn adapter contract.

Useful signs:

- `openapi.json` returns 200 and response keys or body length indicate schema availability.
- `OPTIONS` returns an `allow` header showing POST on a candidate path.
- A candidate POST returns 200/201/202/400/401/403/422 with structured JSON keys.
- Response keys suggest session, turn, message, event, stream, output, reply, or status objects.

Unsafe signs:

- endpoint requires public URL;
- endpoint redirects unexpectedly;
- endpoint requires browser cookies;
- endpoint requires interactive session scraping;
- endpoint response is only HTML login page;
- endpoint returns tool execution or shell execution affordances.

## Release rule

Do not implement real customer reply transport until a private endpoint path, request schema, response schema, timeout behavior, auth/session behavior, and safe parsing contract are confirmed by this gate.
