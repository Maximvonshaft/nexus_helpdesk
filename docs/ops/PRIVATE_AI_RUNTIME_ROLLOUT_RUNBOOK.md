# Private AI Runtime Rollout Runbook

This runbook wires NexusDesk to a server-side AI Runtime without exposing the runtime token to customer browsers or `widget.js`.

## Target Scope

```mermaid
flowchart LR
  visitor["Customer browser"] --> nexus["NexusDesk backend"]
  nexus --> pr["Provider Runtime"]
  pr -->|"traffic authority / kill switch"| private_ai["private_ai_runtime adapter"]
  private_ai -->|"Authorization: Bearer from token file"| ai["AI Runtime or MCS gateway"]
  ai --> direct["/api/chat qwen2.5:3b"]
  ai --> rag["/api/chat qwen3:4b with Nexus RAG context"]
  ai --> tts["/voice/tts"]
  ai --> stt["/voice/stt"]
  browser_voice["WebChat voice-entry.js"] --> edge["same-origin /webchat/live/ws"]
  edge --> voice_gateway["MCS voice gateway"]
  voice_gateway --> live_ws["/live/ws"]
```

The model names above describe the legacy Runtime identity at the time this traffic-authority contract was written. Capability and model identity verification is owned separately by #586; this runbook is not proof that those names match a running Runtime.

## Server Secrets

Create an app-readable, root-managed token file on the server. Do not put the token in `deploy/.env.prod`, nginx config, `widget.js`, or browser-visible HTML.

```bash
install -d -m 0750 -o root -g 101 /opt/nexus_helpdesk/deploy/runtime_secrets
printf '%s' "$AI_RUNTIME_TOKEN" > /opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token
chown 100:101 /opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token
chmod 0400 /opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token
unset AI_RUNTIME_TOKEN
```

The compose templates mount that file read-only to `/run/nexus/ai_runtime_token`. Rotate the token before production cutover if it has been shared in chat, logs, screenshots, or shell history.

## Candidate Env

```env
PRIVATE_AI_RUNTIME_ENABLED=true
PRIVATE_AI_RUNTIME_BASE_URL=http://47.87.143.41:18081
PRIVATE_AI_RUNTIME_RAG_BASE_URL=http://rag-ai-runtime.internal:18081
PRIVATE_AI_RUNTIME_ALLOW_SHARED_RAG_MODEL=false
PRIVATE_AI_RUNTIME_TOKEN_FILE=/run/nexus/ai_runtime_token
PRIVATE_AI_RUNTIME_DIRECT_PATH=/api/chat
PRIVATE_AI_RUNTIME_RAG_PATH=/api/chat
PRIVATE_AI_RUNTIME_CHAT_MODE=direct
PRIVATE_AI_RUNTIME_REQUEST_SHAPE=ollama_chat
PRIVATE_AI_RUNTIME_DIRECT_MODEL=qwen2.5:3b
PRIVATE_AI_RUNTIME_RAG_MODEL=qwen3:4b
PRIVATE_AI_RUNTIME_DIRECT_MODEL_POLICY=fixed
PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS=20
PRIVATE_AI_RUNTIME_MAX_PROMPT_CHARS=3500
PRIVATE_AI_RUNTIME_MAX_OUTPUT_CHARS=1200
PRIVATE_AI_RUNTIME_OLLAMA_KEEP_ALIVE=30m

PROVIDER_RUNTIME_PRIMARY_PROVIDER=private_ai_runtime
PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]
PROVIDER_RUNTIME_OUTPUT_CONTRACT=nexus_webchat_runtime_reply_v1
PROVIDER_RUNTIME_TIMEOUT_MS=30000
PROVIDER_RUNTIME_TRAFFIC_MODE=control
PROVIDER_RUNTIME_CANARY_PERCENT=0
PROVIDER_RUNTIME_KILL_SWITCH=false
```

The committed candidate and production examples intentionally default to `control` plus `0`. Copying an example must never grant candidate authority. Shadow and canary are explicit rollout mutations after their gates pass.

Keep `PRIVATE_AI_RUNTIME_CHAT_MODE=direct` for customer-facing WebChat unless the heavier RAG model has its own isolated Runtime host. In production, Nexus fails closed if `rag|auto` would load a different RAG model on the same Runtime origin while `PRIVATE_AI_RUNTIME_ALLOW_SHARED_RAG_MODEL=false`.

## Provider Traffic Authority

`PROVIDER_RUNTIME_TRAFFIC_MODE` is the server-owned authority that gives `PROVIDER_RUNTIME_CANARY_PERCENT` its meaning:

- `canary`: calculate a stable bucket from server-owned Tenant, channel, session/conversation and scenario identity. The candidate is authoritative only when `bucket < canary_percent`.
- `shadow`: call and validate the candidate, record bounded audit evidence, then discard its output. Shadow output cannot become customer-visible and cannot execute a tool, create a ticket, enqueue work, or perform an external action.
- `control`: do not call the candidate. The Router returns an explicit unavailable/control result so the governed caller retains its approved control behavior.
- `PROVIDER_RUNTIME_KILL_SWITCH=true`: emergency authority that prevents the candidate call. A valid true kill switch remains effective even when lower-priority mode, percentage, or persisted values are malformed; those defects remain visible in bounded evidence.

The exact traffic bucket contract is:

```text
sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100
```

The contract excludes random state, request IDs, and worker identity. Reconstructing the same scoped request after a retry, worker change, or restart yields the same bucket. Changing Tenant, Tenant key, channel, session/conversation, or scenario may deliberately produce a different bucket.

Audit rows include only bounded `traffic_selection` evidence: schema version, configured mode, configuration errors, percentage, bucket, path, authoritative flag, and reason. No customer message, token, or upstream payload belongs in this summary.

Traffic configuration is fail-closed. Unsupported or explicitly empty mode, non-canonical/non-integer/out-of-range percentage, non-boolean persisted kill switch, and persisted drift masked by an environment override prevent a candidate call. Nexus does not silently clamp, coerce, or substitute a permissive value.

WebCall Provider compatibility aliases `router` and `private_ai_runtime` both route through `ProviderRuntimeRouter`; a direct adapter bypass is prohibited.

## WebCall AI Providers

```env
WEBCALL_AI_PRODUCTION_ENABLED=true
WEBCALL_AI_AGENT_ENABLED=true
WEBCALL_AI_PUBLIC_ROLLOUT_MODE=internal
WEBCALL_AI_PROVIDER_PROFILE=external
STT_PROVIDER=external
LLM_PROVIDER=external
TTS_PROVIDER=external
STT_ENDPOINT=http://47.87.143.41:18081/voice/stt
LLM_ENDPOINT=http://47.87.143.41:18081/chat/direct
TTS_ENDPOINT=http://47.87.143.41:18081/voice/tts
STT_API_KEY_FILE=/run/nexus/ai_runtime_token
LLM_API_KEY_FILE=/run/nexus/ai_runtime_token
TTS_API_KEY_FILE=/run/nexus/ai_runtime_token
TTS_VOICE=af_heart
```

## Knowledge Runtime

Only enable OpenAI-compatible embeddings after confirming the Runtime exposes `/v1/embeddings` and the vector dimension:

```env
KNOWLEDGE_RUNTIME_VERSION=v2
KNOWLEDGE_EMBEDDINGS_ENABLED=true
KNOWLEDGE_EMBEDDING_PROVIDER=openai_compatible
KNOWLEDGE_EMBEDDING_BASE_URL=http://47.87.143.41:18081/v1
KNOWLEDGE_EMBEDDING_API_KEY_FILE=/run/nexus/ai_runtime_token
KNOWLEDGE_EMBEDDING_MODEL=BAAI/bge-m3
KNOWLEDGE_EMBEDDING_DIM=<confirmed_dimension>
KNOWLEDGE_VECTOR_FALLBACK_ALLOWED=false
```

If the Runtime only supports `/rag/search` and `/rag/upsert`, keep Nexus pgvector retrieval enabled and route RAG answers through `PRIVATE_AI_RUNTIME_CHAT_MODE=rag` or `auto` until the native adapter is approved.

## Smoke

```bash
python backend/scripts/smoke_private_ai_runtime.py \
  --base-url http://47.87.143.41:18081 \
  --token-file /run/nexus/ai_runtime_token \
  --request-shape ollama_chat \
  --include-rag \
  --include-live-health \
  --include-tts
```

Warm the customer-facing direct model before public traffic or after restarting app/worker containers:

```bash
python scripts/smoke/warm_private_ai_runtime.py
```

In Docker deployments:

```bash
docker compose --env-file deploy/.env.prod -f deploy/docker-compose.server.yml \
  exec -T app python /app/scripts/smoke/warm_private_ai_runtime.py
```

Treat warmup as a deployment gate, not a container healthcheck. Expected timings and actual model identity must come from #586 capability proof rather than stale names in this document.

Then run candidate WebChat smoke. Provider audit rows must show the expected `traffic_selection.path`, no secret values, and parse rejects, health skips, and timeouts must retain bounded traffic evidence and fail closed.

## Cutover

1. Start with `PROVIDER_RUNTIME_TRAFFIC_MODE=control` and `PROVIDER_RUNTIME_CANARY_PERCENT=0`. Prove no candidate call occurs.
2. Set `PROVIDER_RUNTIME_TRAFFIC_MODE=shadow`. Pass smoke and inspect bounded `shadow_generate` audit rows; prove no customer reply or side effect is produced from Shadow output.
3. Set `PROVIDER_RUNTIME_TRAFFIC_MODE=canary` while keeping the percentage at `0`; confirm the Control path remains authoritative.
4. Raise canary to `1`, then `5`, then `25`, then `100`, with a defined observation window and rollback threshold at each step.
5. Keep `PROVIDER_RUNTIME_FALLBACK_PROVIDERS=[]`; backend fallback must return `reply:null`, not customer-visible text.
6. Roll back instantly with:

```env
PROVIDER_RUNTIME_KILL_SWITCH=true
```

A valid true kill switch is higher priority than Control, Shadow, Canary, percentage validation, mode validation, and persisted drift. It suppresses candidate execution while still surfacing lower-priority defects.

## Production Gates

- Token exists only in a server-side file.
- Browser traces contain no Runtime URL, bearer token, or upstream WS token.
- Traffic mode, percentage, bucket contract, Admin status, and audit path consistently use `nexus.provider_runtime.traffic_selection.v1`.
- Invalid effective or persisted configuration is `misconfigured` and performs no candidate call.
- `0%` never sends an authoritative candidate request.
- Identical server-owned scope maps to the same bucket across retries, workers, and restarts.
- Shadow output never becomes customer-visible and never performs a side effect.
- A valid true kill switch suppresses every candidate call, including when lower-priority settings are malformed.
- Health skips, timeouts, and parse rejects retain bounded traffic evidence.
- WebChat returns valid `nexus_webchat_runtime_reply_v1` output only on an authoritative candidate path.
- Live tracking status is never claimed without trusted tracking evidence.
- WebCall voice remains same-origin through `/webchat/live/ws`.
- Runtime capability/model identity is proven through #586 before rollout.
- RAG embedding dimension is confirmed before production vector writes.
