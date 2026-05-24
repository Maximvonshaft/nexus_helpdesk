# Code X Chat Smoke Runbook

This runbook proves Nexus can use an authorized Code X credential for a real admin-only chat call. It does not mark the system production-ready; that requires a deployed nonce smoke call to pass.

Current deployment fact: the status endpoint is proven and `smoke-chat` is deployed. The previous blocker was `codex_llm_endpoint_not_configured`, which means the OAuth credential is authorized but no callable Code X LLM runtime endpoint was configured for the backend.

Current deployed state after the bridge service rollout: the `18794` `codex-app-server-bridge` process is alive, but `/readyz` returns `503 codex_app_server_real_upstream_not_configured` because no real Code X app-server upstream is running or configured on `18795`.

This PR now includes a production-manageable `18795` upstream proxy. The proxy still does not generate replies by itself. It forwards the bridge payload and OAuth bearer token to a configured private Code X reply endpoint, validates the strict Fast Reply JSON response, and fails closed when the private endpoint is missing, unhealthy, slow, or returns invalid output.

## Preconditions

- Admin authentication is available.
- The admin user has `runtime.manage`.
- `PROVIDER_CREDENTIAL_ENCRYPTION_KEY_FILE` is mounted and readable by the backend.
- At least one active `openai-codex` OAuth credential exists for the tenant.
- Preferred existing runtime path:
  - `CODEX_APP_SERVER_BRIDGE_URL=http://codex-app-server-bridge:18794/reply`.
  - `CODEX_APP_SERVER_LOGIN_URL=http://codex-app-server-bridge:18794/login`.
  - `CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token`.
  - Optional: `CODEX_APP_SERVER_TIMEOUT_MS`.
- Direct approved LLM endpoint path:
  - `CODEX_LLM_ENDPOINT`.
  - `CODEX_LLM_API_STYLE=openai_chat` or `CODEX_LLM_API_STYLE=responses`.
  - Optional: `CODEX_LLM_MODEL`, `CODEX_LLM_TIMEOUT_SECONDS`, `CODEX_LLM_RETRIES`.
- Backward compatibility only: `CODEX_SMOKE_ENDPOINT`, `CODEX_SMOKE_MODEL`, and `CODEX_SMOKE_TIMEOUT_MS` are still accepted, but new deployments should use `CODEX_APP_SERVER_*` or `CODEX_LLM_*`.

Do not configure provider access tokens, refresh tokens, client secrets, or encryption keys inline.

The backend obtains the Code X OAuth access token through the existing provider credential encryption/decryption and `OAuthRefreshManager` path. Do not configure an OpenAI API key for this probe.

## Bridge Service

Start the production-managed bridge service:

```bash
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-app-server-upstream codex-app-server-bridge
```

The compose service runs `deploy/codex_app_server_bridge_proxy.py` on port `18794`, bound to the Docker bridge host address:

```bash
BIND_HOST=0.0.0.0
PORT=18794
CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token
CODEX_APP_SERVER_BRIDGE_MODE=real
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://codex-app-server-upstream:18795/reply
CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=codex_app_server
CODEX_APP_SERVER_UPSTREAM_TIMEOUT_SECONDS=30
CODEX_APP_SERVER_READYZ_TIMEOUT_SECONDS=2
```

If the real app-server upstream is host-local instead of Docker-network reachable, set:

```bash
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://127.0.0.1:18795/reply
```

Do not point this bridge at the OpenClaw Responses proxy on `18793`; that is not accepted as Code X proof.

`/healthz` is liveness only and returns `200` when the bridge process is alive. `/readyz` is strict and checks the token file, `CODEX_APP_SERVER_BRIDGE_MODE=real`, `CODEX_APP_SERVER_REAL_UPSTREAM_URL`, and the upstream `/readyz` endpoint. It may return `503` while `/healthz` returns `200`.

If no real upstream is configured, `/readyz` and `/reply` fail closed with:

```text
codex_app_server_real_upstream_not_configured
```

If a real upstream URL is configured but not reachable or not ready, `/readyz` fails closed with:

```text
codex_app_server_real_upstream_unreachable
```

## Required 18795 Upstream

The repository contains a production-managed upstream proxy at `deploy/codex_app_server_private_upstream_proxy.py`. It exposes:

- `GET /healthz`: process liveness.
- `GET /readyz`: strict readiness. It returns HTTP `200` only when a private reply endpoint is configured, reachable through its `/readyz`, and `CODEX_APP_SERVER_REPLY_GENERATION_BACKEND` is not `unconfigured`.
- `POST /reply`: requires the bridge-forwarded OAuth bearer token, forwards the turn payload to the private Code X reply endpoint, and returns only strict Fast Reply JSON.

Run the proxy on `18795` through compose:

```bash
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-app-server-upstream
```

The preferred Nexus-owned private reply endpoint is `codex-private-reply-engine` on `18796`. It is still reply-only and does not generate fake replies. It accepts the OAuth bearer from the 18795 proxy, calls the configured official OpenClaw Codex harness adapter on `18800`, validates strict Fast Reply JSON, and fails closed if the runtime is missing, unhealthy, slow, or returns invalid output.

Run the 18796 engine, the 18800 OpenClaw Codex harness adapter, and point the 18795 proxy at 18796:

```bash
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-private-model-runtime codex-private-reply-engine codex-app-server-upstream codex-app-server-bridge
```

Required server env for the Nexus-owned endpoint:

```bash
CODEX_APP_SERVER_PRIVATE_REPLY_URL=http://codex-private-reply-engine:18796/reply
CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=nexus_private_reply_engine
CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL=http://codex-private-model-runtime:18800/reply
CODEX_PRIVATE_REPLY_ENGINE_MODEL_TIMEOUT_SECONDS=30
CODEX_PRIVATE_REPLY_ENGINE_READYZ_TIMEOUT_SECONDS=30
OPENCLAW_CODEX_RUNTIME_ENABLED=true
OPENCLAW_CODEX_CLI=openclaw
OPENCLAW_CODEX_AUTH_PROVIDER=openai-codex
OPENCLAW_CODEX_PLUGIN_PACKAGE=@openclaw/codex
OPENCLAW_CODEX_MODEL=openai/gpt-5.5
OPENCLAW_CODEX_INFER_TRANSPORT=gateway
OPENCLAW_CODEX_REPLY_TIMEOUT_SECONDS=60
```

Then point the `18794` bridge at the proxy:

```bash
CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://codex-app-server-upstream:18795/reply
```

If the private endpoint already exposes the exact bridge contract and strict JSON response, the bridge can point directly at it instead:

```bash
CODEX_APP_SERVER_REAL_UPSTREAM_URL=<private Code X /reply endpoint>
CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=<real backend label>
```

Required private reply endpoint contract:

- `GET /healthz`: process liveness.
- `GET /readyz`: returns HTTP `200` with ready state only when the endpoint can call the real Code X app-server runtime.
- `POST /reply`: accepts the OAuth bearer token and the bridge payload:
  - `body`
  - `messages`
  - `contract`
  - `tracking_fact_summary`
  - `tracking_fact_evidence_present`
  - `chatgptAccountId`
  - `chatgptPlanType`
- `POST /reply`: performs the actual Code X-backed LLM call and returns strict Fast Reply JSON:
  - `reply`
  - `intent`
  - `tracking_number`
  - `handoff_required`
  - `handoff_reason`
  - `recommended_agent_action`

The endpoint must not use browser cookie scraping, ChatGPT session scraping, shell/tool execution, direct customer/ticket/order actions, an OpenAI API key, fixture output, hardcoded nonce echo, or OpenClaw `18793` as proof. If the OpenClaw Codex harness adapter behind `CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL` is absent, the correct state is blocked, not successful.

## Official OpenClaw Codex Harness Adapter Behind 18796

`CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL` is the private deployment target for `codex-private-model-runtime`, the Nexus HTTP adapter around the official OpenClaw Codex runtime. It is not the 18796 engine itself and it must not be a fixture, stub, or hardcoded nonce echo service. The expected production shape is a private HTTP service reachable only inside the Docker network, host private network, or equivalent VPC segment:

```bash
CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL=http://codex-private-model-runtime:18800/reply
```

The adapter runs `deploy/codex_openclaw_codex_harness_adapter.py` on port `18800`. It uses the official OpenClaw CLI and plugin path:

- OpenClaw CLI package: `openclaw`.
- Codex plugin package: `@openclaw/codex`.
- Auth profile: `openclaw models auth login --provider openai-codex`.
- P0 reply proof: `openclaw infer model run ... --json` through the OpenClaw-managed provider/auth/runtime path.

OpenClaw documentation backing this shape:

- `openclaw models auth list --provider openai-codex` and `openclaw models status` are the documented Codex OAuth checks.
- `openclaw infer model run --json` is the documented headless provider-backed inference surface.
- Codex harness mode keeps OpenClaw responsible for routing/delivery while Codex owns the native model loop.

The 18796 engine derives readiness from the origin of that URL and probes:

```text
GET http://codex-private-model-runtime:18800/readyz
```

The 18800 adapter exposes:

- `GET /healthz`: process liveness only.
- `GET /readyz`: strict readiness. It returns HTTP `200` only when the OpenClaw CLI is installed, `@openclaw/codex` is visible, `openai-codex` auth is present, a real model is configured, and the adapter is explicitly enabled.
- `POST /reply`, accepting the 18796-forwarded payload:
  - `body`
  - `messages`
  - `contract`
  - `tracking_fact_summary`
  - `tracking_fact_evidence_present`
  - `chatgptAccountId`
  - `chatgptPlanType`
  - `response_contract`
- `POST /reply` requires the forwarded OAuth bearer token for the Nexus chain boundary, then calls OpenClaw's official Codex runtime through fixed argv with `shell=False`.
- `POST /reply` must return strict Fast Reply JSON:
  - `reply`
  - `intent`
  - `tracking_number`
  - `handoff_required`
  - `handoff_reason`
  - `recommended_agent_action`

Required adapter env:

```bash
HOME=/home/appuser
OPENCLAW_HOME=/home/appuser/.openclaw
XDG_CONFIG_HOME=/home/appuser/.openclaw
OPENCLAW_CODEX_RUNTIME_ENABLED=true
OPENCLAW_CODEX_CLI=openclaw
OPENCLAW_CODEX_AUTH_PROVIDER=openai-codex
OPENCLAW_CODEX_PLUGIN_PACKAGE=@openclaw/codex
OPENCLAW_CODEX_REQUIRE_PLUGIN=true
OPENCLAW_CODEX_MODEL=openai/gpt-5.5
OPENCLAW_CODEX_INFER_TRANSPORT=gateway
OPENCLAW_CODEX_READY_TIMEOUT_SECONDS=30
OPENCLAW_CODEX_REPLY_TIMEOUT_SECONDS=60
```

The 18800 service persists the official OpenClaw auth/profile home through this read/write bind mount:

```text
/opt/nexus_helpdesk/deploy/runtime_secrets/openclaw_codex_home:/home/appuser/.openclaw:rw
```

The compose profile includes `codex-openclaw-home-permissions`, a one-shot root init container that creates the mount target and applies `appuser:appgroup` ownership before `codex-private-model-runtime` starts. Do not log or copy files from this directory; it may contain OAuth material after login.

Before enabling the service, install and authenticate OpenClaw through the official route inside the same mounted home:

```bash
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d codex-openclaw-home-permissions codex-private-model-runtime

docker compose -f deploy/docker-compose.server.yml exec codex-private-model-runtime \
  openclaw models auth login --provider openai-codex --set-default

docker compose -f deploy/docker-compose.server.yml exec -T codex-private-model-runtime \
  openclaw models auth list --provider openai-codex --json

docker compose -f deploy/docker-compose.server.yml exec -T codex-private-model-runtime \
  openclaw models status --json
```

The adapter may be promoted later:

- P0: CLI adapter proof using official OpenClaw Codex auth/profile/runtime.
- P1: persistent OpenClaw Gateway adapter.
- P2: direct Codex app-server protocol adapter.

The adapter must not use browser cookie scraping, ChatGPT session scraping, shell command strings, arbitrary tool execution, direct ticket/order/customer writes, OpenClaw `18793`, OpenAI API keys, fixture responses, or hardcoded nonce echo logic.

When the admin smoke prompt asks the model to echo a nonce, `nonce_echoed=true` is valid only if the real model output includes that nonce. A deterministic service that copies the nonce out of the request is not acceptable as production proof.

Safe readiness labels:

```bash
CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=nexus_private_reply_engine
```

The 18796 engine treats `unconfigured`, `stub`, and `contract_fixture` backend labels as not ready even if a URL is present.

## Deployed No-Traffic Smoke Gate

Keep production routing safe before and during this smoke:

```bash
# DB route must remain no-traffic for Code X.
# Expected: primary_provider=codex_app_server, fallback includes openclaw_responses, canary_percent=0.
docker compose -f deploy/docker-compose.server.yml exec -T app \
  python - <<'PY'
from app.db import SessionLocal
from sqlalchemy import text
db = SessionLocal()
try:
    row = db.execute(text("""
        SELECT primary_provider, fallback_providers, canary_percent
        FROM provider_routing_rules
        WHERE route_name = 'webchat_fast_reply'
        ORDER BY updated_at DESC
        LIMIT 1
    """)).mappings().first()
    print(dict(row or {}))
finally:
    db.close()
PY
```

Expected route state:

```text
canary_percent=0
fallback_providers contains openclaw_responses
```

Start the Code X private path without enabling customer traffic:

```bash
export WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
export CODEX_APP_SERVER_PRIVATE_REPLY_URL=http://codex-private-reply-engine:18796/reply
export CODEX_APP_SERVER_REPLY_GENERATION_BACKEND=nexus_private_reply_engine
export CODEX_PRIVATE_REPLY_ENGINE_MODEL_URL=http://codex-private-model-runtime:18800/reply
export CODEX_APP_SERVER_REAL_UPSTREAM_URL=http://codex-app-server-upstream:18795/reply
export HOME=/home/appuser
export OPENCLAW_HOME=/home/appuser/.openclaw
export XDG_CONFIG_HOME=/home/appuser/.openclaw
export OPENCLAW_CODEX_RUNTIME_ENABLED=true
export OPENCLAW_CODEX_AUTH_PROVIDER=openai-codex
export OPENCLAW_CODEX_PLUGIN_PACKAGE=@openclaw/codex
export OPENCLAW_CODEX_MODEL=openai/gpt-5.5
export OPENCLAW_CODEX_INFER_TRANSPORT=gateway
export OPENCLAW_CODEX_READY_TIMEOUT_SECONDS=30

docker compose -f deploy/docker-compose.server.yml --profile codex-app-server up -d \
  codex-openclaw-home-permissions \
  codex-private-model-runtime \
  codex-private-reply-engine \
  codex-app-server-upstream \
  codex-app-server-bridge
```

Required readiness before nonce smoke:

```bash
docker compose -f deploy/docker-compose.server.yml exec -T codex-private-model-runtime \
  curl -fsS http://127.0.0.1:18800/readyz
# HTTP 200, ok=true

docker compose -f deploy/docker-compose.server.yml exec -T codex-private-reply-engine \
  curl -fsS http://127.0.0.1:18796/readyz
# HTTP 200, ok=true

docker compose -f deploy/docker-compose.server.yml exec -T codex-app-server-upstream \
  curl -fsS http://127.0.0.1:18795/readyz
# HTTP 200, ok=true

docker compose -f deploy/docker-compose.server.yml exec -T codex-app-server-bridge \
  curl -fsS http://127.0.0.1:18794/readyz
# HTTP 200, ok=true
```

Then run the admin-only nonce smoke. The smoke does not enable WebChat customer traffic:

```bash
NONCE="codex-smoke-$(date +%s)"
SMOKE_BODY="$(curl -sS -w '\n%{http_code}' \
  -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  https://www.leakle.com/api/admin/provider-credentials/codex/smoke-chat \
  -d "{\"mode\":\"smoke\",\"nonce\":\"$NONCE\",\"prompt\":\"Echo the nonce exactly for Nexus runtime verification.\"}")"
SMOKE_HTTP_CODE="$(printf '%s' "$SMOKE_BODY" | tail -n1)"
printf '%s\n' "$SMOKE_BODY" | sed '$d'
```

Expected success:

```bash
SMOKE_HTTP_CODE=200
nonce_echoed=True
VERDICT=CODEX_AUTH_AND_CHAT_MODEL_CALL_CONNECTED
```

After the smoke, re-check that customer traffic stayed on fallback:

```text
WEBCHAT_FAST_AI_CODEX_APP_SERVER_ENABLED=false
canary_percent=0
OpenClaw fallback remains configured
```

## Bridge Probes

```bash
curl -fsS http://172.18.0.1:18794/healthz
curl -fsS http://172.18.0.1:18794/readyz
curl -fsS http://172.18.0.1:18795/healthz
curl -fsS http://172.18.0.1:18795/readyz
docker compose -f deploy/docker-compose.server.yml exec -T codex-private-model-runtime curl -fsS http://127.0.0.1:18800/healthz
docker compose -f deploy/docker-compose.server.yml exec -T codex-private-model-runtime curl -fsS http://127.0.0.1:18800/readyz
docker compose -f deploy/docker-compose.server.yml exec -T codex-private-reply-engine curl -fsS http://127.0.0.1:18796/healthz
docker compose -f deploy/docker-compose.server.yml exec -T codex-private-reply-engine curl -fsS http://127.0.0.1:18796/readyz
```

Expected `/readyz` before running smoke:

- HTTP `200`
- `ok=true`
- `mode=real`
- `real_upstream_configured=true`
- `reply_generation_backend=codex_app_server`
- `token_file_configured=true`

## Status Probe

```bash
curl -fsS \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://www.leakle.com/api/admin/provider-credentials/codex/status
```

Expected:

- HTTP `200`
- `secret_values_exposed=false`
- at least one credential with `status=active`

## Smoke Chat Probe

Set the Nexus backend environment and restart the backend:

```bash
CODEX_APP_SERVER_BRIDGE_URL=http://codex-app-server-bridge:18794/reply
CODEX_APP_SERVER_LOGIN_URL=http://codex-app-server-bridge:18794/login
CODEX_APP_SERVER_TOKEN_FILE=/run/nexus/codex_app_server_bridge_token
```

```bash
NONCE="codex-smoke-$(date +%s)"
curl -fsS \
  -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  https://www.leakle.com/api/admin/provider-credentials/codex/smoke-chat \
  -d "{\"mode\":\"smoke\",\"nonce\":\"$NONCE\",\"prompt\":\"Echo the nonce exactly for Nexus runtime verification.\"}"
```

Expected success:

```json
{
  "ok": true,
  "provider": "codex",
  "credential_status": "authorized",
  "model_call_status": "completed",
  "nonce_echoed": true,
  "response_text_redacted": "...",
  "latency_ms": 1234,
  "request_id": "...",
  "warnings": []
}
```

Operational verdict after success:

```bash
SMOKE_HTTP_CODE=200
nonce_echoed=True
VERDICT=CODEX_AUTH_AND_CHAT_MODEL_CALL_CONNECTED
```

## Failure Verdicts

- `401`: authentication required.
- `403`: admin or `runtime.manage` required.
- `404 codex_credential_not_found`: no active authorized Code X credential for the tenant.
- `409 credential_refresh_required`: stored credential could not be refreshed or is expired.
- `503 codex_llm_endpoint_not_configured`: credential is authorized, but no callable `CODEX_APP_SERVER_BRIDGE_URL`, `CODEX_LLM_ENDPOINT`, or backward-compatible `CODEX_SMOKE_ENDPOINT` is configured.
- `503 codex_app_server_real_upstream_not_configured`: bridge is running but no real Code X app-server upstream is configured.
- `503 codex_app_server_real_upstream_unreachable`: bridge has an upstream URL, but the upstream `/readyz` is not reachable or not ready.
- `503 codex_private_reply_endpoint_not_configured`: the `18795` proxy is alive but no private Code X reply endpoint is configured.
- `503 codex_private_reply_endpoint_unreachable`: the private Code X reply endpoint is configured but not ready or unreachable.
- `503 codex_private_reply_model_not_configured`: the `18796` Nexus private reply engine is alive but no private Code X model/reply runtime is configured.
- `503 codex_private_reply_model_unreachable`: the `18796` Nexus private reply engine has a model URL, but that runtime is not ready.
- `503 openclaw_codex_runtime_disabled`: the `18800` OpenClaw Codex harness adapter is installed but not explicitly enabled.
- `503 openclaw_codex_plugin_not_ready`: the `18800` adapter cannot see the official `@openclaw/codex` plugin.
- `503 openclaw_codex_auth_not_ready`: the `18800` adapter cannot confirm an `openai-codex` auth profile through OpenClaw.
- `503 openclaw_codex_model_not_configured`: the `18800` adapter has no real `OPENCLAW_CODEX_MODEL`.
- `502 codex_provider_call_failed`: configured endpoint failed, timed out, returned invalid JSON, or returned an HTTP error.

The response must never include `access_token`, `refresh_token`, authorization headers, client secret, encryption key, or raw credential payload.

## Audit Evidence

Each invocation writes `admin_audit_logs.action=codex_smoke_chat_invoked` with safe metadata only:

- request id
- prompt hash and prompt length
- nonce hash
- credential id hash
- provider status
- model call status
- latency
- actor id

Raw prompt, raw nonce, access token, refresh token, authorization headers, and credential payloads are not stored.

## Rollback

Unset the callable endpoint/bridge and restart the backend:

```bash
unset CODEX_APP_SERVER_BRIDGE_URL
unset CODEX_APP_SERVER_LOGIN_URL
unset CODEX_APP_SERVER_REAL_UPSTREAM_URL
unset CODEX_APP_SERVER_PRIVATE_REPLY_URL
unset CODEX_LLM_ENDPOINT
unset CODEX_SMOKE_ENDPOINT
docker compose -f deploy/docker-compose.server.yml up -d --no-deps app
docker compose -f deploy/docker-compose.server.yml --profile codex-app-server stop codex-app-server-bridge codex-app-server-upstream
```

After rollback, `smoke-chat` should fail closed with `503 codex_llm_endpoint_not_configured` when an authorized credential exists.
